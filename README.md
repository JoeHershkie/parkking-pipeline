# Parking

Toronto parking bylaw data pipeline — map-ready GeoJSON from city open data.

**Documentation:** [docs/README.md](docs/README.md)

**Run pipeline** (from repo root):

```bash
pip install -r requirements.txt
python src/clean_data.py
python src/regex.py
python src/geometry_engine.py
```
