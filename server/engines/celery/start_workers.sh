#!/usr/bin/env bash
echo "Started celery workers with PYTHONPATH: "
echo $PYTHONPATH

celery -A engines.celery.tasks worker --loglevel=debug --logfile=/usr/local/web/WPCDAS/server/logs/worker0.log --config=engines.celery.celeryconfig -c 1 -n worker0.%h &
celery -A engines.celery.tasks worker --loglevel=debug --logfile=/usr/local/web/WPCDAS/server/logs/worker1.log --config=engines.celery.celeryconfig -c 1 -n worker1.%h &
celery -A engines.celery.tasks worker --loglevel=debug --logfile=/usr/local/web/WPCDAS/server/logs/worker2.log --config=engines.celery.celeryconfig -c 1 -n worker2.%h &
celery -A engines.celery.tasks worker --loglevel=debug --logfile=/usr/local/web/WPCDAS/server/logs/worker3.log --config=engines.celery.celeryconfig -c 1 -n worker3.%h &

#celery -A engines.celery.tasks flower &
#echo "Started admin web server"

#celery -A engines.celery.tasks worker -Q wq0,celery --loglevel=debug --concurrency 1 -n worker0.%h &
#celery -A engines.celery.tasks worker -Q wq1 --loglevel=debug --concurrency 1 -n worker1.%h &
#celery -A engines.celery.tasks worker -Q wq2 --loglevel=debug --concurrency 1 -n worker2.%h &
#celery -A engines.celery.tasks worker -Q wq3 --loglevel=debug --concurrency 1 -n worker3.%h &

#celery -A engines.celery.tasks worker --loglevel=debug --concurrency 1 -n worker3.%h
#celery -A engines.celery.tasks worker --loglevel=debug --concurrency 1 -n worker4.%h
#celery -A engines.celery.tasks worker --loglevel=debug --concurrency 1 -n worker5.%h
#celery -A engines.celery.tasks worker --loglevel=debug --concurrency 1 -n worker6.%h
#celery -A engines.celery.tasks worker --loglevel=debug --concurrency 1 -n worker7.%h
#celery -A engines.celery.tasks worker --loglevel=debug --concurrency 1 -n worker8.%h

#celery -A tasks worker --loglevel=debug  --concurrency 1 -n worker2.%h &
#celery -A tasks worker --loglevel=debug  --concurrency 1 -n worker3.%h &
#celery -A tasks worker --loglevel=debug  --concurrency 1 -n worker4.%h &


