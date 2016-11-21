#!/bin/bash

base="/usr/local/ophidia"
prim="$base/oph-cluster/oph-primitives"
anal="$base/oph-cluster/oph-analytics-framework"
srv="$base/oph-server"
passwd="abcd"

configure_mysql() {
  service mysql start

  mysqladmin -u root password "$passwd"

  mysql -u root --password=abcd mysql < "$prim/etc/create_func.sql"
  mysql -u root --password=abcd mysql -e "create database ophidiadb;"
  mysql -u root --password=abcd mysql -e "create database oph_dimensions;"
  mysql -u root --password=abcd ophidiadb < "$anal/etc/ophidiadb.sql"
  mysql -u root --password=abcd ophidiadb -e "INSERT INTO host (hostname, cores, memory) VALUES ('127.0.0.1', 1, 1);"
  mysql -u root --password=abcd ophidiadb -e "INSERT INTO dbmsinstance (idhost, login, password, port) VALUES (1, 'root', '$passwd', 3306);"
  mysql -u root --password=abcd ophidiadb -e "INSERT INTO hostpartition (partitionname) VALUES ('test');"
  mysql -u root --password=abcd ophidiadb -e "INSERT INTO hashost (idhostpartition, idhost) VALUES (1, 1);"
}

configure_server() {
  openssl req -newkey rsa:1024 \
    -passout pass:abcd \
    -subj "/" -sha1 \
    -keyout rootkey.pem \
    -out rootreq.pem
  openssl x509 -req -in rootreq.pem \
    -passin pass:abcd \
    -sha1 -extensions v3_ca \
    -signkey rootkey.pem \
    -out rootcert.pem
  cat rootcert.pem rootkey.pem  > cacert.pem

  cp cacert.pem "$srv/etc/cert"

  openssl req -newkey rsa:1024 \
    -passout pass:abcd \
    -subj "/" -sha1 \
    -keyout serverkey.pem \
    -out serverreq.pem
  openssl x509 -req \
    -in serverreq.pem \
    -passin pass:abcd \
    -sha1 -extensions usr_cert \
    -CA cacert.pem  \
    -CAkey cacert.pem \
    -CAcreateserial \
    -out servercert.pem
  cat servercert.pem serverkey.pem rootcert.pem > myserver.pem

  cp myserver.pem "$srv/etc/cert"

cat << EOF > "/root/miniconda2/etc/slurm.conf"
# slurm.conf file generated by configurator easy.html.
# Put this file on all nodes of your cluster.
# See the slurm.conf man page for more information.
#
ControlMachine=$(hostname)
#ControlAddr=
# 
#MailProg=/bin/mail 
MpiDefault=none
#MpiParams=ports=#-# 
ProctrackType=proctrack/pgid
ReturnToService=1
SlurmctldPidFile=/var/run/slurmctld.pid
#SlurmctldPort=6817 
SlurmdPidFile=/var/run/slurmd.pid
#SlurmdPort=6818 
SlurmdSpoolDir=/var/spool/slurmd
SlurmUser=root
#SlurmdUser=root 
StateSaveLocation=/var/spool
SwitchType=switch/none
TaskPlugin=task/none
# 
# 
# TIMERS 
#KillWait=30 
#MinJobAge=300 
#SlurmctldTimeout=120 
#SlurmdTimeout=300 
# 
# 
# SCHEDULING 
FastSchedule=1
SchedulerType=sched/backfill
#SchedulerPort=7321 
SelectType=select/linear
# 
# 
# LOGGING AND ACCOUNTING 
AccountingStorageType=accounting_storage/none
ClusterName=cluster
#JobAcctGatherFrequency=30 
JobAcctGatherType=jobacct_gather/none
#SlurmctldDebug=3 
#SlurmctldLogFile=
#SlurmdDebug=3 
#SlurmdLogFile=
# 
# 
# COMPUTE NODES 
NodeName=$(hostname) CPUs=1 State=UNKNOWN 
PartitionName=debug Nodes=$(hostname) Default=YES MaxTime=INFINITE State=UP
EOF

  dd if=/dev/urandom bs=1 count=1024 > /etc/munge/munge.key
  chmod 0400 /etc/munge/munge.key

  mkdir -p /var/run/munge
  chmod 0755 /var/run/munge
  mkdir -p /var/log/munge
  chmod 0700 /var/log/munge
  mkdir -p /var/lib/munge
  chmod 0711 /var/lib/munge
  mkdir -p /etc/munge
  chmod 0700 /etc/munge

  chown -R munge:munge /etc/munge
  chown -R munge:munge /var/lib/munge
  chown -R munge:munge /var/log/munge
  chown -R munge:munge /var/run/munge

  echo 'OPTIONS="--force"' >> /etc/default/munge

  service munge start
}

configure_mysql

configure_server

ssh-keygen -t dsa -f ~/.ssh/id_dsa -N ''
cat ~/.ssh/id_dsa.pub >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys

echo -e "export PATH=/root/miniconda2/bin:$PATH\n$(cat ~/.bashrc)" > ~/.bashrc

service ssh start

ssh -o "StrictHostKeyChecking no" 127.0.0.1 hostname

slurmctld
slurmd

oph_server &>/dev/null &

echo "alias oph_term='oph_term -H 127.0.0.1 -P 11732 -u oph-test -p abcd'" >> ~/.bashrc

exec "$@"
