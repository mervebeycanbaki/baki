        raise RuntimeError(f"Binance HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Binance baglanti hatasi: {exc.reason}") from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Binance yaniti gecerli JSON degil.") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("symbols"), list):
        raise RuntimeError(f"Binance yaniti beklenen formatta degil: {str(payload)[:500]}")
    return payload


def load_latest_cmc_snapshot() -> tuple[list[dict], Path]:
    files = sorted(DATA_DIR.glob("????-??-??.csv"))
    if not files:
        raise RuntimeError("data/daily klasorunde YYYY-MM-DD.csv dosyasi bulunamadi.")
    latest_file = files[-1]
    with latest_file.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        required = {"cmc_id", "name", "symbol"}
        if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
            raise RuntimeError(
                f"{latest_file} dosyasinda cmc_id, name, symbol sutunlari bulunamadi."
            )
        coins = []
        for row in reader:
            cmc_id = (row.get("cmc_id") or "").strip()
            name = (row.get("name") or "").strip()
            symbol = (row.get("symbol") or "").strip().upper()
            if cmc_id and symbol:
                coins.append({"cmc_id": cmc_id, "name": name, "cmc_symbol": symbol})
    if not coins:
        raise RuntimeError(f"{latest_file} dosyasinda gecerli coin kaydi bulunamadi.")
    return coins, latest_file


def build_binance_symbol_map(exchange_info: dict) -> dict[str, list[str]]:
    pairs_by_base: dict[str, list[str]] = defaultdict(list)
    for item in exchange_info["symbols"]:
        if not isinstance(item, dict):
            continue
        permissions = item.get("permissions") or []
        permission_sets = item.get("permissionSets") or []
        has_spot_permission = (
            "SPOT" in permissions
            or any("SPOT" in group for group in permission_sets if isinstance(group, list))
        )
        if (
            item.get("status") == "TRADING"
            and str(item.get("quoteAsset") or "").upper() == "USDT"
            and (item.get("isSpotTradingAllowed") is True or has_spot_permission)
        ):
            base = str(item.get("baseAsset") or "").strip().upper()
            pair = str(item.get("symbol") or "").strip().upper()
            if base and pair:
                pairs_by_base[base].append(pair)
    return {base: sorted(set(pairs)) for base, pairs in pairs_by_base.items()}


def main() -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    coins, latest_file = load_latest_cmc_snapshot()
    print(f"CMC veri dosyasi: {latest_file}")
    print(f"CMC coin kaydi: {len(coins)}")
    print("Binance aktif USDT spot pariteleri aliniyor...")

    exchange_info = get_json(BINANCE_EXCHANGE_INFO_URL)
    pairs_by_base = build_binance_symbol_map(exchange_info)
    cmc_symbol_counts = Counter(coin["cmc_symbol"] for coin in coins)
    rows = []
    counts = Counter()

    for coin in coins:
        ticker = coin["cmc_symbol"]
        candidates = pairs_by_base.get(ticker, [])
        if cmc_symbol_counts[ticker] > 1:
            status, pair = "AMBIGUOUS_CMC_SYMBOL", ""
        elif len(candidates) == 1:
            status, pair = "MATCHED", candidates[0]
        elif len(candidates) == 0:
            status, pair = "NO_BINANCE_USDT_PAIR", ""
        else:
            status, pair = "MULTIPLE_BINANCE_USDT_PAIRS", ""
        counts[status] += 1
        rows.append({
            "cmc_id": coin["cmc_id"],
            "name": coin["name"],
            "cmc_symbol": ticker,
            "binance_symbol": pair,
            "status": status,
        })

    fieldnames = ["cmc_id", "name", "cmc_symbol", "binance_symbol", "status"]
    with OUTPUT_CSV.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    matched = [row for row in rows if row["status"] == "MATCHED"]
    ambiguous = counts["AMBIGUOUS_CMC_SYMBOL"] + counts["MULTIPLE_BINANCE_USDT_PAIRS"]
    no_pair = counts["NO_BINANCE_USDT_PAIR"]
    summary_lines = [
        "BINANCE COIN ESLESTIRME RAPORU",
        f"CMC veri dosyasi: {latest_file.name}",
        f"Toplam CMC coin kaydi: {len(coins)}",
        f"Tekil Binance USDT spot paritesi bulunan: {len(matched)}",
        f"Binance USDT paritesi bulunamayan: {no_pair}",
        f"Belirsiz eslesme nedeniyle atlanan: {ambiguous}",
        "",
        "Ilk 30 eslesen coin:",
    ]
    for row in matched[:30]:
        summary_lines.append(f'{row["cmc_symbol"]} | {row["name"]} | {row["binance_symbol"]}')
    SUMMARY_FILE.write_text("\n".join(summary_lines) + "\n", encoding="utf-8")

    if not OUTPUT_CSV.is_file() or OUTPUT_CSV.stat().st_size == 0:
        raise RuntimeError(f"Eslesme CSV dosyasi olusturulamadi: {OUTPUT_CSV}")
    print("\n".join(summary_lines))
    print(f"\nCSV hazir: {OUTPUT_CSV}")
    print(f"Ozet hazir: {SUMMARY_FILE}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"HATA: {exc}")
        raise SystemExit(1)
