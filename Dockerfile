FROM ubuntu:latest
LABEL authors="vasiltkach"

ENTRYPOINT ["top", "-b"]