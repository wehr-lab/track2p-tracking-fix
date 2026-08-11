#!/bin/bash
#SBATCH --job-name=copy_2P_data
#SBATCH --account=wehrlab    ### change this to your actual account for charging
#SBATCH --partition=compute       ### queue to submit to
#SBATCH --output=/projects/wehrlab/wehr/batchlogs/copy_2P_data_%j.log
#SBATCH --error=/projects/wehrlab/wehr/batcherrors/copy_2P_data-%A_%a_err.txt       ### file in which to store job stderr
#SBATCH --cpus-per-task=1
#SBATCH --mem=1G
#SBATCH --nodes=1               ### number of nodes to use
#SBATCH --ntasks-per-node=1     ### number of tasks to launch per node
#SBATCH --time=23:00:00
#SBATCH --output=/projects/wehrlab/wehr/batchlogs/copy_2P_data_%j.log

rsync -avP  wehr@wehr-nas.uoregon.edu:/share/Projects/2P5XFAD/JarascopeData/wehr5670 /scratch/wehrlab/2P5XFAD

#rsync -avP  --exclude="/track2p/*" --exclude="/*-002/*" wehr@wehr-nas.uoregon.edu:/share/#Projects/2P5XFAD/JarascopeData/wehr5408 /scratch/wehrlab/2P5XFAD

