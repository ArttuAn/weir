.PHONY: test demo run origin live clean

test:
	python3 -m unittest discover -s tests -v

demo:
	python3 -m weir demo

run:
	python3 -m weir run --route agent=http://127.0.0.1:8711/

origin:
	python3 -m weir origin

live:
	python3 scripts/live_check.py

clean:
	find . -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
	rm -f *.receipts
