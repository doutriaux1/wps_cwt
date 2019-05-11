#! /bin/bash

function cleanup() {
  kill -15 ${celery_pid} &>/dev/null

  echo "Killed celery"

  wait ${celery_pid}

  kill -15 ${metrics_pid} &>/dev/null

  echo "Killed metrics"

  wait ${metrics_pid}

  rm -rf ${CWT_METRICS}
}

trap cleanup SIGINT SIGTERM

pushd /compute

celery worker -A compute ${@} &

celery_pid=$!

if [[ -n "${CWT_METRICS}" ]]; then
  [[ ! -e "${CWT_METRICS}" ]] && mkdir "${CWT_METRICS}"

  python -m wps.tasks.metrics_ &

  metrics_pid=$!
fi

wait
