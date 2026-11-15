
name: CMC API Test

on:
  workflow_dispatch:

permissions:
  contents: read

jobs:
  test:
    runs-on: ubuntu-latest

    steps:
      - name: Repository indir
        uses: actions/checkout@v4

      - name: Python kur
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Gerekli kutuphaneyi kur
        run: python -m pip install requests

      - name: CoinMarketCap API testi
        env:
          CMC_API_KEY: ${{ secrets.CMC_API_KEY }}
        run: python test_cmc_api.py
