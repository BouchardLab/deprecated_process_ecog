#!/bin/bash -l
python -u read_chang_data.py /project/projectdirs/m2043/BRAINdata/Humans/EC9 15 39 46 49 53 60 63 --zscore 'events' --output_folder $SCRATCH/output
