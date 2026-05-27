import os
import pandas as pd
from pathlib import Path

# Benchmarks calibrated for Secret Outlet / fashion ecommerce.
# CTR values are percentage points from GSC, e.g. 0.80 means 0.80%.
PAGE_CTR_MIN_DESIRED = 0.80
PAGE_CTR_TARGET_DESIRED = 1.20
AVG_POSITION_TARGET = 6.00

BRAND_ALIASES = {
    "tommy hilfiger": ["tommy hilfiger", "tommy"],
    "tommy jeans": ["tommy jeans"],
    "lacoste": ["lacoste"],
    "calvin klein": ["calvin klein"],
    "aramis": ["aramis"],
    "reserva": ["reserva"],
    "john john": ["john john"],
    "levis": ["levis", "levi's", "levi"],
    "dudalina": ["dudalina"],
    "columbia": ["columbia"],
    "diesel": ["diesel"],
    "osklen": ["osklen"],
    "ellus": ["ellus"],
    "individual": ["individual"],
    "sergio k": ["sergio k"],
    "replay": ["replay"],
    "puma": ["puma"],
    "crocs": ["crocs"],
    "colcci": ["colcci"],
    "foxton": ["foxton"],
    "nike": ["nike"],
    "adidas": ["adidas"],
    "hollister": ["hollister"],
    "fred perry": ["fred perry"],
    "aleatory": ["aleatory"],
    "nautica": ["nautica"],
    "secret outlet": ["secret outlet", "secretoutlet", "secrets outlet", "outlet secret", "secret"],
}

BRAND_TERMS = {
    token
    for aliases in BRAND_ALIASES.values()
    for alias in aliases
    for token in alias.replace("'", "").split()
}

PRODUCT_TERMS = {
    "tenis", "tênis", "polo", "polos", "camisa", "camisas", "camiseta",
    "camisetas", "moletom", "moletons", "jaqueta", "jaquetas", "calca",
    "calça", "calcas", "calças", "bone", "boné", "bones", "chinelos",
    "chinelo",
}

COMMERCIAL_TERMS = {
    "outlet", "loja", "comprar", "preco", "preço", "promocao", "promoção",
    "desconto", "sale", "masculino", "masculina", "original",
}


def _norm(text: str) -> str:
    return str(text or "").lower().replace("-", " ").replace("'", "").strip()


def _tokens(text: str) -> set:
    return set(_norm(text).split())


def _detect_brand(text: str) -> str:
    value = f" {_norm(text)} "
    pairs = [
        (brand, alias)
        for brand, aliases in BRAND_ALIASES.items()
        for alias in aliases
    ]
    for brand, alias in sorted(pairs, key=lambda item: len(item[1]), reverse=True):
        if f" {alias} " in value:
            return brand
    return ""


def _has_product(text: str) -> bool:
    return bool(_tokens(text) & PRODUCT_TERMS)


def _has_commercial_modifier(text: str) -> bool:
    return bool(_tokens(text) & COMMERCIAL_TERMS)


def _is_fashion_brand_product(text: str) -> bool:
    tokens = _tokens(text)
    return bool(tokens & BRAND_TERMS or tokens & PRODUCT_TERMS)


def classify_query(text: str) -> str:
    brand = _detect_brand(text)
    has_product = _has_product(text)
    has_commercial = _has_commercial_modifier(text)
    token_count = len(_tokens(text))

    if brand == "secret outlet":
        return "brand_generic" if token_count <= 3 else "brand_navigational"
    if brand and has_product:
        return "product_brand"
    if brand and has_commercial:
        return "brand_commercial"
    if brand and token_count <= 2:
        return "brand_generic"
    if brand:
        return "brand_navigational"
    if has_product:
        return "product_generic"
    return "generic"


def _expected_page_ctr(text: str = "") -> float:
    return PAGE_CTR_MIN_DESIRED if _is_fashion_brand_product(text) else PAGE_CTR_TARGET_DESIRED


def _ctr_status(value: float) -> str:
    if value >= PAGE_CTR_TARGET_DESIRED:
        return "above_target"
    if value >= PAGE_CTR_MIN_DESIRED:
        return "within_target"
    return "below_target"


def _position_status(value: float) -> str:
    return "ok" if value < AVG_POSITION_TARGET else "below_benchmark"


def _clean_df(df: pd.DataFrame, kind: str) -> pd.DataFrame:
    # Normalize column names: strip whitespace, lowercase for matching
    df.columns = [c.strip() for c in df.columns]

    # Map any GSC column name variant to a standard internal name
    QUERY_COLS  = {"top consultas", "consulta", "query", "queries", "top queries"}
    PAGE_COLS   = {"páginas principais", "página", "page", "top pages", "paginas principais"}
    CLICKS_COLS = {"cliques", "clicks"}
    IMP_COLS    = {"impressões", "impressions", "impresses"}
    CTR_COLS    = {"ctr"}
    POS_COLS    = {"posição", "position", "posicao"}
    DATE_COLS   = {"data", "date"}

    rename = {}
    for col in df.columns:
        cl = col.lower().strip()
        if cl in QUERY_COLS:   rename[col] = "query"
        elif cl in PAGE_COLS:  rename[col] = "page"
        elif cl in CLICKS_COLS: rename[col] = "clicks"
        elif cl in IMP_COLS:   rename[col] = "impressions"
        elif cl in CTR_COLS:   rename[col] = "ctr"
        elif cl in POS_COLS:   rename[col] = "position"
        elif cl in DATE_COLS:  rename[col] = "date"
    df = df.rename(columns=rename)
    if "ctr" in df.columns:
        df["ctr"] = (df["ctr"].astype(str)
                     .str.replace("%", "", regex=False)
                     .str.replace(",", ".", regex=False)
                     .astype(float))
    if "position" in df.columns:
        df["position"] = (df["position"].astype(str)
                          .str.replace(",", ".", regex=False)
                          .astype(float))
    if "clicks" in df.columns:
        df["clicks"] = pd.to_numeric(df["clicks"], errors="coerce").fillna(0).astype(int)
    if "impressions" in df.columns:
        df["impressions"] = pd.to_numeric(df["impressions"], errors="coerce").fillna(0).astype(int)
    return df


def load_folder(folder: str) -> dict:
    """Load all CSVs from a GSC export folder. Handles accented filenames."""
    folder = Path(folder)
    candidates = {
        "consultas": ["Consultas.csv"],
        "paginas":   ["Páginas.csv", "Paginas.csv", "Páginas.csv"],
        "grafico":   ["Gráfico.csv", "Grafico.csv", "Gráfico.csv"],
        "dispositivos": ["Dispositivos.csv"],
        "filtros":   ["Filtros.csv"],
    }
    data = {}
    for key, names in candidates.items():
        for name in names:
            path = folder / name
            if path.exists():
                try:
                    df = pd.read_csv(path, encoding="utf-8-sig")
                    df.columns = [c.strip() for c in df.columns]
                    data[key] = df
                    break
                except Exception:
                    try:
                        df = pd.read_csv(path, encoding="latin-1")
                        df.columns = [c.strip() for c in df.columns]
                        data[key] = df
                        break
                    except Exception:
                        pass
    return data


# ── Analysis functions ────────────────────────────────────────────────────────

def ctr_opportunities(consultas: pd.DataFrame, min_imp: int = 300) -> pd.DataFrame:
    """Query CTR is intentionally not benchmarked.

    CTR decisions are made at page level. Queries are used for content and
    intent opportunities instead of creating title/meta tasks from arbitrary
    CTR targets.
    """
    columns = ["query", "clicks", "impressions", "ctr", "position"]
    return pd.DataFrame(columns=columns)


def query_content_opportunities(consultas: pd.DataFrame, min_imp: int = 200) -> pd.DataFrame:
    """Queries that can become content, category or internal-link tasks."""
    df = consultas.copy()
    df["query_type"] = df["query"].apply(classify_query)
    df["content_action"] = df["query_type"].map({
        "brand_generic": "Monitorar marca e reforcar pagina de marca/categoria",
        "brand_navigational": "Monitorar intencao navegacional; evitar pauta de blog isolada",
        "brand_commercial": "Otimizar landing/categoria comercial da marca",
        "product_brand": "Criar ou otimizar guia de compra para produto + marca",
        "product_generic": "Criar guia comparativo, FAQ ou hub de categoria",
        "generic": "Avaliar pauta editorial se houver intencao informacional",
    })
    df["opportunity_score"] = (
        (df["impressions"] / 1000)
        + df["clicks"] * 0.5
        + df["position"].apply(lambda p: max(0, 14 - float(p or 0)) * 3)
    ).round(1)
    df["potential_clicks"] = df["opportunity_score"]
    return (df[
            (df["impressions"] >= min_imp)
            & (df["position"] <= 20)
            & (~df["query_type"].isin(["brand_generic", "brand_navigational"]))
        ]
            .sort_values("opportunity_score", ascending=False)
            [["query", "clicks", "impressions", "ctr", "position", "query_type", "content_action", "opportunity_score"]]
            .head(50))


def position_opportunities(consultas: pd.DataFrame, min_imp: int = 200) -> pd.DataFrame:
    """Queries in positions 5–15 with meaningful volume."""
    df = consultas.copy()
    df = df[(df["impressions"] >= min_imp) & df["position"].between(AVG_POSITION_TARGET, 15)].copy()
    df["target_position"] = AVG_POSITION_TARGET
    df["position_gap"] = df["position"] - AVG_POSITION_TARGET
    df["priority"] = df.apply(
        lambda r: "ALTA"  if r["position"] <= 8  and r["impressions"] >= 1000 else
                  "MÉDIA" if r["position"] <= 12 and r["impressions"] >= 500  else "BAIXA",
        axis=1)
    df["action"] = df["position"].apply(
        lambda p: "Melhorar snippet e alinhamento da pagina" if p <= 9 else "Conteúdo on-page + link building")
    return df.sort_values(["priority", "impressions"], ascending=[True, False]).head(40)


def quick_wins(consultas: pd.DataFrame) -> pd.DataFrame:
    """Positions 7–12 — one optimization away from page 1 top."""
    df = consultas.copy()
    df = df[df["position"].between(AVG_POSITION_TARGET, 10) & (df["impressions"] >= 200)].copy()
    df["target_position"] = AVG_POSITION_TARGET
    df["position_gap"] = df["position"] - AVG_POSITION_TARGET
    df["action"] = df["position"].apply(
        lambda p: "Melhorar resposta da pagina para a query" if p <= 9 else "Melhorar conteúdo + links internos")
    return df.sort_values("impressions", ascending=False).head(25)


def low_ctr_pages(paginas: pd.DataFrame, min_imp: int = 1000) -> pd.DataFrame:
    """Category/brand pages with high impressions but low CTR."""
    df = paginas.copy()
    df["expected_ctr"] = df["page"].apply(_expected_page_ctr)
    df["ctr_gap"] = df["expected_ctr"] - df["ctr"]
    df["potential_clicks"] = ((df["ctr_gap"] / 100) * df["impressions"]).astype(int)
    df["benchmark"] = df["page"].apply(
        lambda q: "moda/marca/produto >=0.80%" if _is_fashion_brand_product(q) else "geral 0.80%-1.20%"
    )
    return (df[(df["impressions"] >= min_imp) & (df["ctr_gap"] >= 0.20)]
            .sort_values("potential_clicks", ascending=False)
            [["page", "clicks", "impressions", "ctr", "position", "expected_ctr", "benchmark", "ctr_gap", "potential_clicks"]]
            .head(20))


def benchmark_summary(queries: pd.DataFrame = None, pages: pd.DataFrame = None,
                      trend: dict = None) -> dict:
    queries = queries if queries is not None else pd.DataFrame()
    pages = pages if pages is not None else pd.DataFrame()
    trend = trend or {}

    def weighted_ctr(df: pd.DataFrame) -> float:
        if df.empty or "impressions" not in df or not int(df["impressions"].sum() or 0):
            return 0.0
        clicks = float(df["clicks"].sum() or 0)
        impressions = float(df["impressions"].sum() or 0)
        return round(clicks / impressions * 100, 2)

    fashion_queries = queries[
        queries["query"].apply(_is_fashion_brand_product)
    ] if not queries.empty and "query" in queries else pd.DataFrame()
    fashion_pages = pages[
        pages["page"].apply(_is_fashion_brand_product)
    ] if not pages.empty and "page" in pages else pd.DataFrame()

    avg_ctr = trend.get("avg_ctr") or weighted_ctr(queries)
    avg_position = trend.get("avg_position")
    if avg_position is None and not queries.empty and "position" in queries:
        avg_position = round(queries["position"].mean(), 2)
    avg_position = float(avg_position or 0)

    fashion_ctr = weighted_ctr(fashion_queries)
    fashion_position = round(fashion_queries["position"].mean(), 2) if not fashion_queries.empty else 0.0
    if not fashion_position and not fashion_pages.empty:
        fashion_position = round(fashion_pages["position"].mean(), 2)

    return {
        "page_ctr_desired_range": "0.80%-1.20%",
        "page_ctr_min_desired": PAGE_CTR_MIN_DESIRED,
        "page_ctr_target_desired": PAGE_CTR_TARGET_DESIRED,
        "query_ctr_targets": {},
        "query_ctr_policy": "Queries nao usam benchmark fixo de CTR; servem para analise de intencao e conteudo.",
        "avg_position_target": AVG_POSITION_TARGET,
        "avg_ctr": round(float(avg_ctr or 0), 2),
        "avg_ctr_status": _ctr_status(float(avg_ctr or 0)),
        "fashion_ctr": fashion_ctr,
        "fashion_ctr_status": "informational",
        "avg_position": avg_position,
        "avg_position_status": _position_status(avg_position),
        "fashion_position": fashion_position,
        "fashion_position_status": _position_status(fashion_position) if fashion_position else "no_data",
    }


def brand_reachability(queries: pd.DataFrame) -> list[dict]:
    if queries is None or queries.empty or "query" not in queries:
        return []

    rows = []
    df = queries.copy()
    df["brand"] = df["query"].apply(_detect_brand)
    df["query_type"] = df["query"].apply(classify_query)
    df = df[df["brand"] != ""]

    for brand, group in df.groupby("brand"):
        commercial = group[group["query_type"].isin(["product_brand", "brand_commercial"])]
        generic = group[group["query_type"].isin(["brand_generic", "brand_navigational"])]

        def metrics(part):
            if part.empty:
                return 0, 0, 0.0, 0.0
            imp = int(part["impressions"].sum())
            clicks = int(part["clicks"].sum())
            ctr = round(clicks / imp * 100, 2) if imp else 0.0
            pos = round((part["position"] * part["impressions"]).sum() / imp, 2) if imp else 0.0
            return imp, clicks, ctr, pos

        c_imp, c_clicks, c_ctr, c_pos = metrics(commercial)
        g_imp, g_clicks, g_ctr, g_pos = metrics(generic)

        if c_imp >= 50000 and c_pos <= 8:
            reach = "alta"
        elif c_imp >= 10000 and c_pos <= 10:
            reach = "media"
        else:
            reach = "baixa"

        rows.append({
            "brand": brand,
            "reachable_priority": reach,
            "commercial_impressions": c_imp,
            "commercial_clicks": c_clicks,
            "commercial_ctr": c_ctr,
            "commercial_position": float(c_pos),
            "generic_impressions": g_imp,
            "generic_clicks": g_clicks,
            "generic_ctr": g_ctr,
            "generic_position": float(g_pos),
            "note": "Priorizar produto+marca/outlet; marca pura e navegacional entra como monitoramento.",
        })

    priority_order = {"alta": 0, "media": 1, "baixa": 2}
    return sorted(rows, key=lambda r: (priority_order.get(r["reachable_priority"], 9), -r["commercial_impressions"]))


def trend_analysis(grafico: pd.DataFrame) -> dict:
    df = grafico.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)

    mid = len(df) // 2
    f, s = df.iloc[:mid], df.iloc[mid:]

    def pct_change(a, b):
        return round((b - a) / a * 100, 1) if a else 0

    f_clicks = f["clicks"].mean()
    s_clicks = s["clicks"].mean()
    f_imp = f["impressions"].mean()
    s_imp = s["impressions"].mean()

    best_idx = df["clicks"].idxmax()

    return {
        "total_clicks":       int(df["clicks"].sum()),
        "total_impressions":  int(df["impressions"].sum()),
        "avg_ctr":            round(df["ctr"].mean(), 2),
        "avg_ctr_status":     _ctr_status(round(df["ctr"].mean(), 2)),
        "avg_position":       round(df["position"].mean(), 2),
        "avg_position_status": _position_status(round(df["position"].mean(), 2)),
        "ctr_benchmark":      "paginas 0.80%-1.20%; queries por intencao",
        "position_benchmark": "< 6.00",
        "clicks_trend":       pct_change(f_clicks, s_clicks),
        "impressions_trend":  pct_change(f_imp, s_imp),
        "best_day":           df.loc[best_idx, "date"].strftime("%Y-%m-%d"),
        "best_day_clicks":    int(df.loc[best_idx, "clicks"]),
        "daily_labels":       [d.strftime("%d/%m") for d in df["date"]],
        "daily_clicks":       df["clicks"].tolist(),
        "daily_impressions":  df["impressions"].tolist(),
        "daily_positions":    df["position"].tolist(),
    }


def cannibalization_hints(paginas: pd.DataFrame) -> pd.DataFrame:
    """Pages whose URL slugs share root keywords — potential cannibalization."""
    from urllib.parse import urlparse
    df = paginas.copy()

    def root(url):
        path = urlparse(str(url)).path.strip("/")
        segs = [s for s in path.split("-") if len(s) > 3]
        return segs[0] if segs else path[:20]

    df["root"] = df["page"].apply(root)
    counts = df.groupby("root")["page"].count()
    multi = counts[counts > 1].index
    return (df[df["root"].isin(multi)]
            .sort_values(["root", "impressions"], ascending=[True, False])
            [["page", "clicks", "impressions", "ctr", "position", "root"]])


# ── API-based entry point ─────────────────────────────────────────────────────

def run_from_api(queries_rows: list, pages_rows: list, time_series: list = None) -> dict:
    """Same analysis as run() but from GSC API row dicts (gsc_api.fetch_raw output).

    Expects CTR already in percentage form (0-100) as produced by fetch_raw().
    """
    results = {"source": "api"}
    queries_clean = None
    pages_clean   = None

    if queries_rows:
        q = pd.DataFrame(queries_rows)
        for col in ("clicks", "impressions"):
            if col in q.columns:
                q[col] = pd.to_numeric(q[col], errors="coerce").fillna(0).astype(int)
        for col in ("ctr", "position"):
            if col in q.columns:
                q[col] = pd.to_numeric(q[col], errors="coerce").fillna(0.0)
        queries_clean = q
        results["total_queries"]      = len(q)
        results["top_queries"]        = q.nlargest(20, "impressions").to_dict("records")
        results["ctr_opps"]           = ctr_opportunities(q).to_dict("records")
        results["content_opps"]       = query_content_opportunities(q).to_dict("records")
        results["pos_opps"]           = position_opportunities(q).to_dict("records")
        results["quick_wins"]         = quick_wins(q).to_dict("records")
        results["brand_reachability"] = brand_reachability(q)

    if pages_rows:
        p = pd.DataFrame(pages_rows)
        for col in ("clicks", "impressions"):
            if col in p.columns:
                p[col] = pd.to_numeric(p[col], errors="coerce").fillna(0).astype(int)
        for col in ("ctr", "position"):
            if col in p.columns:
                p[col] = pd.to_numeric(p[col], errors="coerce").fillna(0.0)
        pages_clean = p
        results["total_pages"]     = len(p)
        results["top_pages"]       = p.nlargest(20, "impressions").to_dict("records")
        results["low_ctr_pages"]   = low_ctr_pages(p).to_dict("records")
        results["cannibalization"] = cannibalization_hints(p).to_dict("records")

    trend = {}
    if time_series:
        ts = pd.DataFrame(time_series)
        if not ts.empty and "date" in ts.columns:
            trend = trend_analysis(ts)
            results["trend"] = trend

    results["benchmarks"] = benchmark_summary(queries_clean, pages_clean, trend)
    return results


# ── CSV-based entry point ─────────────────────────────────────────────────────

def run(folder: str) -> dict:
    raw = load_folder(folder)
    if not raw:
        return {"error": f"Nenhum CSV encontrado em '{folder}'"}

    results = {"folder": folder, "files_found": list(raw.keys())}

    if "consultas" in raw:
        q = _clean_df(raw["consultas"], "consultas")
        queries_clean = q
        results["total_queries"] = len(q)
        results["top_queries"]   = q.nlargest(20, "impressions").to_dict("records")
        results["ctr_opps"]      = ctr_opportunities(q).to_dict("records")
        results["content_opps"]  = query_content_opportunities(q).to_dict("records")
        results["pos_opps"]      = position_opportunities(q).to_dict("records")
        results["quick_wins"]    = quick_wins(q).to_dict("records")
        results["brand_reachability"] = brand_reachability(q)

    if "paginas" in raw:
        p = _clean_df(raw["paginas"], "paginas")
        pages_clean = p
        results["total_pages"]      = len(p)
        results["top_pages"]        = p.nlargest(20, "impressions").to_dict("records")
        results["low_ctr_pages"]    = low_ctr_pages(p).to_dict("records")
        results["cannibalization"]  = cannibalization_hints(p).to_dict("records")

    if "grafico" in raw:
        g = _clean_df(raw["grafico"], "grafico")
        results["trend"] = trend_analysis(g)

    results["benchmarks"] = benchmark_summary(
        locals().get("queries_clean"),
        locals().get("pages_clean"),
        results.get("trend", {}),
    )

    return results
