FROM ghcr.io/astral-sh/uv:python3.13-alpine

WORKDIR /usr/src/app

ENV DOCKER 1

COPY pyproject.toml uv.lock .
RUN uv sync

COPY . .

CMD [ "uv", "run", "theanimebot.py" ]
