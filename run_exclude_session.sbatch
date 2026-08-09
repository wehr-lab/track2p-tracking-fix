#!/bin/bash
#SBATCH --job-name=exclude_session
#SBATCH --account=wehrlab    ### change this to your actual account for charging
#SBATCH --partition=compute       ### queue to submit to
#SBATCH --output=/projects/wehrlab/wehr/batchlogs/exclude_session%j.log
#SBATCH --error=/projects/wehrlab/wehr/batcherrors/exclude_session-%A_%a_err.txt       ### file in which to store job stderr
#SBATCH --cpus-per-task=48
#SBATCH --mem=32G
#SBATCH --nodes=1               ### number of nodes to use
#SBATCH --ntasks-per-node=1     ### number of tasks to launch per node
#SBATCH --time=04:00:00
#SBATCH --output=/projects/wehrlab/wehr/batchlogs/exclude_session_%j.log

# activate your env, e.g.:
source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate track2p

cd /projects/wehrlab/shared/git_repos/track2p-tracking-fix/
python run_exclude_session.py
