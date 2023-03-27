server:
	python src/main.py -s 5000 

client:
	python src/main.py -c client 0.0.0.0 5000 5555

venv:
	python -m venv .venv && source .venv/bin/activate && pip install black

clean:
	deactivate && rm -rf **/__pycache__ .venv
