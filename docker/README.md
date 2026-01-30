 
 mongodump   --host 127.0.0.1:27017   -u root -p 'example' --authenticationDatabase admin   --db jobcenter   --archive=./jobcenter_$(date +%F).archive   --gzip

mongorestore \
  --host new-host:27017 \
  -u root -p 'example' --authenticationDatabase admin \
  --db jobcenter \
  --drop \
  --archive=/backup/jobcenter-08-22.archive \
  --gzip

