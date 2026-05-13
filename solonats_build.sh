#!/bin/bash
for year in 2009 2010 2011 2012 2013 2014 2015 2016 2017 2018 2019 2021; do
  only="${year}_east ${year}_west"
  # Add cam variant for years that have it
  case $year in 2016|2017|2018) only="$only ${year}_cam" ;; esac
  # Add pro variant for years that have it
  case $year in 2011|2015) only="$only ${year}_pro" ;; esac

  python build_track.py --jobs solonats_jobs.json \
    --generated-dir generated/solonats_tracks/lincoln/$year \
    --only $only --fbx
done
