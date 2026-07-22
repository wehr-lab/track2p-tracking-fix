% preflight_registration_check.m
%
% Quick FOV alignment check BEFORE running a full longitudinal session --
% catches a bad FOV/registration in the ~1000-frame acquisition it takes
% to test it, instead of finding out after a full recording plus a suite2p
% run on data you'd end up excluding anyway (see track2p_overview.pptx,
% "Pre-Flight Registration Check" future-direction slide).
%
% WORKFLOW:
%   1. Acquire a SHORT sbx file (N_FRAMES below, ~1000 by default -- a few
%      seconds to ~1 min at typical frame rates) of the new FOV. Scanbox
%      needs the acquisition to finish/close before the file is readable,
%      so this is "acquire short clip -> stop -> run this script -> decide"
%      rather than a truly live check -- still a large win over finding out
%      after a full session + suite2p run.
%   2. Run this script. It averages those frames into a mean projection,
%      registers it against your reference session's ALREADY-COMPUTED
%      suite2p meanImg (no suite2p needed for the NEW session at all), and
%      shows you the same red/green overlay convention used everywhere
%      else in this project (yellow/white = aligned, red/green fringes =
%      not) plus a masked SSIM score.
%   3. Decide: reposition and re-check, or proceed with the full session.
%
% SETUP (one-time, or whenever track_ops.transform_type changes):
%   From the track2p conda env:
%     python export_elastix_params.py --cfg track2p_settings.cfg elastix_params.txt
%   This produces elastix_params.txt (point ELASTIX_PARAMS_FILE at it
%   below) -- see that script's docstring for an important caveat: it's
%   SimpleElastix's own STANDARD parameter map for your transform_type,
%   not a verified line-for-line match to reg_img_elastix.py's exact
%   settings (that source wasn't available when this was written). Diff
%   the two once by hand before trusting this to predict the real
%   pipeline's outcome, and hand-edit elastix_params.txt directly if
%   anything differs -- this script just reads that text file.
%
%   Also needs the REFERENCE session's meanImg exported to .mat -- reuses
%   the existing export_session_qc.py for this, no new export tool needed:
%     python export_session_qc.py /path/to/reference/track2p/save_path --sessions 0
%   (or whatever 0-indexed session you're anchoring against). Produces
%   session_qc.mat; point REFERENCE_QC_MAT below at it.
%
% REQUIRES:
%   - A standalone `elastix` command-line install on THIS machine (the rig
%     computer) -- https://elastix.lumc.nl. Confirm with `elastix --version`
%     in a terminal. This is separate from track2p's own Python/SimpleElastix
%     install; the whole point of this script is to not need Python here.
%   - Image Processing Toolbox (for ssim()).
%   - Your own sbxread.m on the MATLAB path.
%
% CAVEAT -- UNTESTED: written without access to a real .sbx file, a MATLAB
% installation, or reg_img_elastix.py's actual source in the environment
% this was built in (see export_elastix_params.py's docstring). The MHD/RAW
% read/write byte-layout convention used below (row-major, x-fastest,
% matching MATLAB's column-major fwrite(img',...) to ITK's row-major
% expectation) WAS validated independently via a Python round-trip test,
% but the sbxread call, the elastix CLI invocation, and everything in
% between has not been run end-to-end. Treat this as a starting point to
% debug against your actual rig setup, not a finished tool.

%% ---- config -- edit these each time (or move to a separate settings
%%      file, same spirit as this project's Python launcher/settings split,
%%      once this is past the prototype stage) ----

SBX_FILENAME        = 'xx0_000_001';                  % base filename, no extension (sbxread convention)
N_FRAMES             = 1000;                           % frames to read and average -- see module docstring
REG_CHAN             = 1;                               % 1-indexed channel/PMT to use, matching track_ops.reg_chan

REFERENCE_QC_MAT     = 'session_qc.mat';                % from export_session_qc.py --sessions <ref_idx>
ELASTIX_PARAMS_FILE  = 'elastix_params.txt';             % from export_elastix_params.py
ELASTIX_BIN          = 'elastix';                        % full path if not on system PATH
WORK_DIR             = fullfile(tempdir, 'preflight_check');  % scratch dir for MHD files + elastix output

SSIM_SIGNAL_PCTILE   = 80;   % mask to ref's brightest (100-80)=20% of pixels, matching registration_qc_utils.py

%% ---- 1. read + average a short raw acquisition -----------------------

if ~exist(WORK_DIR, 'dir')
    mkdir(WORK_DIR);
end

% ADAPT THIS BLOCK to your actual sbxread.m signature/output shape.
% Assumed here (the common Scanbox/Neurolabware community convention):
%   sbxread(fname, k, N) returns frames k..k+N-1 (k is 0-INDEXED, i.e. 0 =
%   first frame) as a 4D array [nChannels x nRows x nCols x N]. If your
%   version returns a different dimension order (e.g. [nRows x nCols x
%   nChannels x N], or a single channel already selected, or frames along
%   dim 1), fix ONLY extractMeanFrame() below -- everything downstream just
%   expects a plain 2D double image back from it.
fprintf('Reading %d frames from %s...\n', N_FRAMES, SBX_FILENAME);
raw = sbxread(SBX_FILENAME, 0, N_FRAMES);
newImg = extractMeanFrame(raw, REG_CHAN);
fprintf('New-session mean image: %d x %d\n', size(newImg, 1), size(newImg, 2));

%% ---- 2. load the reference session's existing meanImg -----------------

ref = load(REFERENCE_QC_MAT);
refImg = double(ref.meanImg{1});
refLabel = '';
if isfield(ref, 'session_labels')
    refLabel = ref.session_labels{1};
end
fprintf('Reference image (%s): %d x %d\n', refLabel, size(refImg, 1), size(refImg, 2));

if ~isequal(size(refImg), size(newImg))
    error(['Reference and new-session images are different sizes (%dx%d vs %dx%d) -- ' ...
           'FOV/zoom/resolution mismatch, or wrong channel selected? Fix before trusting anything below.'], ...
          size(refImg, 1), size(refImg, 2), size(newImg, 1), size(newImg, 2));
end

%% ---- 3. register newImg onto refImg via the elastix CLI ----------------

refMhdBase = fullfile(WORK_DIR, 'ref');
movMhdBase = fullfile(WORK_DIR, 'mov');
outDir     = fullfile(WORK_DIR, 'out');
if ~exist(outDir, 'dir')
    mkdir(outDir);
end

writeMHD(refImg, refMhdBase);
writeMHD(newImg, movMhdBase);

cmd = sprintf('%s -f %s.mhd -m %s.mhd -p %s -out %s', ...
              ELASTIX_BIN, refMhdBase, movMhdBase, ELASTIX_PARAMS_FILE, outDir);
fprintf('Running: %s\n', cmd);
[status, cmdOut] = system(cmd);
if status ~= 0
    fprintf('%s\n', cmdOut);
    error(['elastix CLI failed (exit %d) -- see output above. Common causes: elastix not on PATH, ' ...
            'ELASTIX_PARAMS_FILE not found, or a malformed parameter file.'], status);
end

resultMhd = fullfile(outDir, 'result.0.mhd');   % matches ResultImageFormat=mhd set by export_elastix_params.py
if ~exist(resultMhd, 'file')
    error('elastix reported success but %s wasn''t created -- check %s for elastix''s own log.', ...
          resultMhd, fullfile(outDir, 'elastix.log'));
end
newImgReg = readMHD(resultMhd);
fprintf('Registration complete.\n');

%% ---- 4. masked SSIM + red/green overlay (same convention as ----------
%%         registration_qc_utils.py / inspect_registration_pair.py) ------

refN = norm01(refImg);
movRegN = norm01(newImgReg);

signalThresh = prctile(refImg(:), SSIM_SIGNAL_PCTILE);
mask = refImg >= signalThresh;

[~, ssimMap] = ssim(movRegN, refN);
if any(mask(:))
    ssimScore = mean(ssimMap(mask));
else
    ssimScore = mean(ssimMap(:));
end
fprintf('\nMasked SSIM (ref''s brightest %d%% of pixels): %.3f\n', 100 - SSIM_SIGNAL_PCTILE, ssimScore);

overlay = zeros(size(refImg, 1), size(refImg, 2), 3);
overlay(:, :, 1) = refN;      % red = ref
overlay(:, :, 2) = movRegN;   % green = registered new session

figure('Name', 'preflight_registration_check', 'Position', [100 100 1400 500]);
subplot(1, 3, 1); imshow(refN); title(sprintf('ref: %s', strrep(refLabel, '_', '\_')));
subplot(1, 3, 2); imshow(movRegN); title('new session (registered)');
subplot(1, 3, 3); imshow(overlay);
title(sprintf('overlay (SSIM=%.3f)\nyellow/white=aligned, red/green fringes=NOT', ssimScore));

outFig = fullfile(WORK_DIR, sprintf('preflight_check_%s.png', datestr(now, 'yyyymmdd_HHMMSS')));
saveas(gcf, outFig);
fprintf('Saved %s\n', outFig);
fprintf(['\nDo NOT decide from the SSIM number alone -- look at the overlay panel. Every other tool in ' ...
         'this project treats the visual check as ground truth over any single automated score, and ' ...
         'this SSIM threshold hasn''t been calibrated against real preflight (short-acquisition, ' ...
         'unprocessed) data the way the post-hoc tools were against full suite2p mean images.\n']);


%% ================= local functions =====================================

function img2d = extractMeanFrame(raw, chan)
    % ADAPT to your sbxread's actual output shape -- see config section
    % comment above. Assumes [nChannels x nRows x nCols x N] in; averages
    % over the frame dimension for the requested channel and returns a
    % plain 2D double image.
    chanStack = squeeze(raw(chan, :, :, :));   % -> [nRows x nCols x N]
    img2d = double(mean(chanStack, 3));
end

function writeMHD(img, basePath)
    % Writes img (a 2D MATLAB matrix) as a MetaImage (.mhd/.raw) pair that
    % elastix can read. Byte layout validated independently (row-major,
    % x-fastest) -- see this script's top-of-file caveat.
    img = single(img);
    [nRows, nCols] = size(img);
    [dirPath, baseName] = fileparts(basePath);
    rawName = [baseName '.raw'];

    fidRaw = fopen(fullfile(dirPath, rawName), 'w');
    fwrite(fidRaw, img', 'float32');   % img' -> column-major write == row-major (x-fastest) on disk
    fclose(fidRaw);

    fidMhd = fopen([basePath '.mhd'], 'w');
    fprintf(fidMhd, 'ObjectType = Image\n');
    fprintf(fidMhd, 'NDims = 2\n');
    fprintf(fidMhd, 'DimSize = %d %d\n', nCols, nRows);   % MHD DimSize is (x, y) = (ncols, nrows)
    fprintf(fidMhd, 'ElementType = MET_FLOAT\n');
    fprintf(fidMhd, 'ElementByteOrderMSB = False\n');
    fprintf(fidMhd, 'ElementSpacing = 1 1\n');
    fprintf(fidMhd, 'ElementDataFile = %s\n', rawName);
    fclose(fidMhd);
end

function img = readMHD(mhdPath)
    % Reads an elastix-produced .mhd/.raw pair back into a 2D MATLAB matrix
    % (inverse of writeMHD).
    fid = fopen(mhdPath, 'r');
    dimSize = [];
    rawFile = '';
    while true
        line = fgetl(fid);
        if ~ischar(line), break; end
        if startsWith(line, 'DimSize')
            dimSize = sscanf(line, 'DimSize = %d %d');   % [ncols; nrows]
        elseif startsWith(line, 'ElementDataFile')
            parts = strsplit(line, '= ');
            rawFile = strtrim(parts{2});
        end
    end
    fclose(fid);
    if isempty(dimSize) || isempty(rawFile)
        error('Could not parse DimSize/ElementDataFile from %s', mhdPath);
    end

    [dirPath, ~] = fileparts(mhdPath);
    fidRaw = fopen(fullfile(dirPath, rawFile), 'r');
    nCols = dimSize(1); nRows = dimSize(2);
    raw = fread(fidRaw, nCols * nRows, 'float32=>double');
    fclose(fidRaw);
    img = reshape(raw, [nCols, nRows])';   % inverse of the write-side transpose
end

function out = norm01(img)
    % Same convention as registration_qc_utils.py's norm01(): clip to the
    % 1st-99th percentile, scale to [0, 1].
    lo = prctile(img(:), 1);
    hi = prctile(img(:), 99);
    if hi <= lo
        out = zeros(size(img));
    else
        out = min(max((img - lo) / (hi - lo), 0), 1);
    end
end
