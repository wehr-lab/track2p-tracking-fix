#!/bin/bash
#SBATCH --job-name=copy_2P_data
#SBATCH --account=wehrlab    ### change this to your actual account for charging
#SBATCH --partition=compute       ### queue to submit to
#SBATCH --output=/projects/wehrlab/wehr/batchlogs/copy_2P_data-%j.log
#SBATCH --error=/projects/wehrlab/wehr/batchlogs/copy_2P_data-j.err       ### file in which to store job stderr
#SBATCH --cpus-per-task=1
#SBATCH --mem=1G
#SBATCH --nodes=1               ### number of nodes to use
#SBATCH --ntasks-per-node=1     ### number of tasks to launch per node
#SBATCH --time=23:00:00
#SBATCH --output=/projects/wehrlab/wehr/batchlogs/copy_2P_data_%j.log

rsync -avP --relative /scratch/wehrlab/2P5XFAD/./wehr*/gap* wehr@wehr-nas.uoregon.edu:/share/Projects/2P5XFAD/DriftOutput/


