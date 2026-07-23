.PHONY: install test lint demo serve worker docker-up docker-down

install:
	python3 -m pip install '.[dev]'

test:
	python3 -m pytest -q

lint:
	python3 -m ruff check .

demo:
	sputnik demo --output reports/demo

serve:
	uvicorn sputnik.api:app --host 127.0.0.1 --port 8765 --reload

worker:
	sputnik worker --poll-seconds 5

docker-up:
	docker compose up --build -d api worker

docker-down:
	docker compose down
