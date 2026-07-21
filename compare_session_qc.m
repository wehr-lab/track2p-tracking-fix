% compare_session_qc.m
%
% Loads session_qc.mat (produced by export_session_qc.py) and:
%   1) shows the mean images for the requested sessions (default 6/7/8)
%      side by side for visual comparison
%   2) bar-plots detected-cell (iscell) counts across all 9 sessions and
%      flags any session sitting well below the others
%
% Run export_session_qc.py first (from your track2p conda env):
%   python export_session_qc.py /path/to/existing/track2p/save_path --sessions 6 7 8
% then point MAT_FILE below at the resulting session_qc.mat.

MAT_FILE = 'session_qc.mat';

data = load(MAT_FILE);

%% 1) mean images side by side
n_imgs = numel(data.meanImg);
ncols = ceil(sqrt(n_imgs));
nrows = ceil(n_imgs / ncols);
figure('Name', 'Mean image comparison', 'Position', [100 100 400*ncols 400*nrows]);
tiledlayout(nrows, ncols, "TileSpacing","compact")

for i = 1:n_imgs
    nexttile
    img = double(data.meanImg{i});
    imagesc(img);
    axis image off;
    colormap(gca, 'gray');

    % robust contrast scaling (1st-99th percentile) so one dim/noisy
    % session doesn't get washed out relative to the others when compared
    lo = prctile(img(:), 1);
    hi = prctile(img(:), 99);
    if hi > lo
        caxis([lo, hi]);  % use clim([lo,hi]) instead on MATLAB R2022a+
    end

    title(strrep(data.session_labels{i}, '_', '\_'), 'Interpreter', 'tex');
end
sgtitle('Mean images (1st-99th percentile contrast)');

%% 2) detected cell counts across ALL sessions
figure('Name', 'iscell counts per session', 'Position', [100 600 900 400]);
counts = double(data.iscell_counts(:));
bar(counts, 'FaceColor', [0.3 0.5 0.8]);
xticks(1:numel(counts));
xticklabels(cellfun(@(s) strrep(s, '_', '\_'), data.all_labels, 'UniformOutput', false));
xtickangle(45);
ylabel('# detected cells (iscell)');
title('Detected cell count per session');
grid on;

% flag sessions sitting well below the group (candidate quality issues)
med_count = median(counts);
outliers = find(counts < 0.5 * med_count);
if ~isempty(outliers)
    hold on;
    bar(outliers, counts(outliers), 'FaceColor', [0.8 0.2 0.2]);
    hold off;
    fprintf('Sessions with <50%% of median cell count (potential quality issue):\n');
    for i = outliers(:)'
        fprintf('  %s: %d cells (group median = %.0f)\n', data.all_labels{i}, counts(i), med_count);
    end
else
    fprintf('No session is below 50%% of the median cell count (%.0f).\n', med_count);
end
