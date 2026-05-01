#!/bin/bash
for year in 2009 2010 2013 2014 2015 2018 2019 2021; do
  python build_track.py --jobs solonats_jobs.json \
    --generated-dir generated/solonats_tracks/lincoln/$year \
    --only ${year}_east ${year}_west --fbx
  done

