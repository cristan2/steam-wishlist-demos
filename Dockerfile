FROM python:3.10.6-slim-buster
WORKDIR /usr/src/app
COPY requirements.txt requirements.txt
RUN pip3 install -r requirements.txt
COPY . .
ENTRYPOINT [ "python" ]
CMD [ "app.py" ]