# -*- coding: utf-8 -*-
"""PChome × MOMO 商品價格比較分析 (Streamlit)

執行方式：
    pip install -r requirements.txt
    streamlit run app.py
"""
from __future__ import annotations

import random
import re
import time
from datetime import datetime

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
import streamlit as st
from curl_cffi import requests as cffi_requests

# ----------------------------------------------------------------------
# 基本設定
# ----------------------------------------------------------------------
st.set_page_config(page_title="PChome × MOMO 價格比較", page_icon="🛒", layout="wide")

PCHOME_API = "https://ecshweb.pchome.com.tw/search/v3.3/all/results"
PCHOME_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

MOMO_API = "https://apisearch.momoshop.com.tw/momoSearchCloud/moec/textSearch"
MOMO_HOME = "https://www.momoshop.com.tw/"
MOMO_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    "Content-Type": "application/json;charset=UTF-8",
    "Origin": "https://www.momoshop.com.tw",
    "Referer": "https://www.momoshop.com.tw/",
}

PLATFORMS = ["PChome", "MOMO"]
PLATFORM_COLORS = {"PChome": "#EF553B", "MOMO": "#636EFA"}

# 兩個平台共用同一組價格區間，圖表才有可比性
PRICE_BINS = [0, 500, 1000, 1500, 2000, 3000, 5000, float("inf")]
PRICE_LABELS = [
    "0~500",
    "501~1,000",
    "1,001~1,500",
    "1,501~2,000",
    "2,001~3,000",
    "3,001~5,000",
    "5,001以上",
]
COLUMNS = ["平台", "頁數", "品名", "價格"]


# ----------------------------------------------------------------------
# 爬蟲
# ----------------------------------------------------------------------
def _sleep(delay: float) -> None:
    if delay > 0:
        time.sleep(random.uniform(delay, delay * 1.6))


@st.cache_data(show_spinner=False, ttl=60 * 10)
def crawl_pchome(keyword: str, pages: int, delay: float) -> tuple[pd.DataFrame, list[str]]:
    rows, errors = [], []
    for page in range(1, pages + 1):
        try:
            params = {"q": keyword, "page": page, "sort": "sale/dc"}
            resp = requests.get(PCHOME_API, params=params, headers=PCHOME_HEADERS, timeout=15)
            resp.raise_for_status()
            prods = resp.json().get("prods", [])
            if not prods:
                errors.append(f"[PChome] 第 {page} 頁：沒有資料（可能已超過最後一頁）")
                break
            for p in prods:
                rows.append(
                    {"平台": "PChome", "頁數": page, "品名": p.get("name", ""), "價格": p.get("price")}
                )
        except Exception as e:  # noqa: BLE001
            errors.append(f"[PChome] 第 {page} 頁：{e}")
        if page < pages:
            _sleep(delay)
    return _to_frame(rows), errors


def _momo_payload(keyword: str, page: int) -> dict:
    return {
        "host": "momoshop",
        "flag": "searchEngine",
        "data": {
            "searchValue": keyword,
            "searchType": "1",
            "cateLevel": "-1",
            "curPage": str(page),
            "priceS": "0",
            "priceE": "9999999",
            "showType": "chessboardType",
            "isFuzzy": "0",
            "rtnCateDatainfo": {
                "cateCode": "",
                "cateLv": "-1",
                "keyword": keyword,
                "curPage": str(page),
                "historyDoPush": "false",
                "timestamp": int(time.time() * 1000),
            },
            "flag": 2018,
            "serviceCode": "MT01",
            "addressSearchData": {},
        },
    }


@st.cache_data(show_spinner=False, ttl=60 * 30)
def crawl_momo(keyword: str, pages: int, delay: float) -> tuple[pd.DataFrame, list[str]]:
    rows, errors = [], []
    try:
        session = cffi_requests.Session(impersonate="chrome")  # 模擬 Chrome TLS 指紋
        session.get(MOMO_HOME, headers=MOMO_HEADERS)  # 先取得 cookie
    except Exception as e:  # noqa: BLE001
        return _to_frame(rows), [f"[MOMO] 連線失敗：{e}"]

    for page in range(1, pages + 1):
        try:
            resp = session.post(MOMO_API, headers=MOMO_HEADERS, json=_momo_payload(keyword, page))
            if resp.status_code != 200:
                errors.append(f"[MOMO] 第 {page} 頁：HTTP {resp.status_code}")
                continue
            goods = resp.json().get("rtnSearchData", {}).get("goodsInfoList", [])
            if not goods:
                errors.append(f"[MOMO] 第 {page} 頁：沒有資料（可能已超過最後一頁）")
                break
            for g in goods:
                rows.append(
                    {
                        "平台": "MOMO",
                        "頁數": page,
                        "品名": g.get("goodsName", ""),
                        "價格": g.get("goodsPrice", ""),
                    }
                )
        except Exception as e:  # noqa: BLE001
            errors.append(f"[MOMO] 第 {page} 頁：{e}")
        if page < pages:
            _sleep(delay)
    return _to_frame(rows), errors


# ----------------------------------------------------------------------
# 資料處理
# ----------------------------------------------------------------------
def parse_price(raw) -> int | None:
    """'$1,273'、1273、'1273' → 1273；失敗回傳 None。"""
    digits = re.sub(r"[^\d]", "", str(raw))
    return int(digits) if digits else None


def _to_frame(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=COLUMNS)
    df["價格"] = df["價格"].map(parse_price)
    df = df.dropna(subset=["價格"]).reset_index(drop=True)
    df["價格"] = df["價格"].astype(int)
    return df


def extract_brand(name: str) -> str:
    """品名多半為「【品牌】商品名稱」或「[品牌] 商品名稱」。"""
    m = re.search(r"【(.+?)】", name) or re.match(r"^\s*\[(.+?)\]", name)
    return m.group(1).strip() if m else "未標示品牌"


def add_features(df: pd.DataFrame, top_n: int) -> pd.DataFrame:
    df = df.copy()
    df["價格區間"] = pd.cut(
        df["價格"], bins=PRICE_BINS, labels=PRICE_LABELS, right=True, include_lowest=True
    ).astype(str)
    df["品牌"] = df["品名"].map(extract_brand)
    # 兩平台合併計算前 N 大品牌，確保品牌分類一致
    labeled = df.loc[df["品牌"] != "未標示品牌", "品牌"]
    top = labeled.value_counts().head(top_n).index
    df["品牌"] = df["品牌"].where(df["品牌"].isin(top) | (df["品牌"] == "未標示品牌"), "其他品牌")
    df["序號"] = df.groupby("平台").cumcount() + 1  # 各平台內的搜尋結果排序
    return df


# ----------------------------------------------------------------------
# 介面：側邊欄
# ----------------------------------------------------------------------
st.title("🛒 PChome × MOMO 商品價格比較")
st.caption("輸入產品名稱與爬取頁數，同時抓取兩大平台並產生折線圖、圓餅圖、旭日圖與柱狀圖。")

with st.sidebar:
    st.header("搜尋條件")
    with st.form("search_form"):
        keyword = st.text_input("產品名稱", value="行動電源")
        pages = st.number_input("爬取頁數（每個平台）", min_value=1, max_value=10, value=2, step=1)
        selected = st.multiselect("比較平台", PLATFORMS, default=PLATFORMS)
        delay = st.slider("每頁間隔秒數", 0.0, 5.0, 1.0, 0.5, help="頁數多時建議拉長，避免被網站封鎖")
        submitted = st.form_submit_button("開始爬取", type="primary")
    top_n = st.slider("圖表顯示前 N 大品牌", 3, 15, 8, help="調整後不需要重新爬取")

if submitted:
    kw_in = keyword.strip()
    if not kw_in:
        st.warning("請輸入產品名稱")
    elif not selected:
        st.warning("請至少選擇一個平台")
    else:
        frames, all_errs = [], []
        with st.spinner(f"正在爬取「{kw_in}」前 {int(pages)} 頁…"):
            if "PChome" in selected:
                d, e = crawl_pchome(kw_in, int(pages), float(delay))
                frames.append(d)
                all_errs += e
            if "MOMO" in selected:
                d, e = crawl_momo(kw_in, int(pages), float(delay))
                frames.append(d)
                all_errs += e
        df_all = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=COLUMNS)
        st.session_state["result"] = (kw_in, df_all, all_errs)

if "result" not in st.session_state:
    st.info("請在左側輸入條件後按「開始爬取」。")
    st.stop()

kw, df_raw, errs = st.session_state["result"]

for e in errs:
    st.warning(e)

if df_raw.empty:
    st.error("沒有取得任何資料，可能被網站阻擋或關鍵字無結果，請稍後再試。")
    st.stop()

df = add_features(df_raw, top_n)
present = [p for p in PLATFORMS if p in df["平台"].unique()]
cmap = {p: PLATFORM_COLORS[p] for p in present}

# ----------------------------------------------------------------------
# 統計指標
# ----------------------------------------------------------------------
stats = (
    df.groupby("平台")["價格"]
    .agg(商品數量="count", 平均價格="mean", 中位數="median", 最高價格="max", 最低價格="min")
    .reindex(present)
)

for col, p in zip(st.columns(len(present)), present):
    s = stats.loc[p]
    with col:
        st.subheader(p)
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("商品數", f"{int(s['商品數量']):,}")
        m2.metric("平均價", f"${s['平均價格']:,.0f}")
        m3.metric("最高價", f"${int(s['最高價格']):,}")
        m4.metric("最低價", f"${int(s['最低價格']):,}")

tab_line, tab_pie, tab_sun, tab_bar, tab_data = st.tabs(
    ["📈 折線圖", "🥧 圓餅圖", "☀️ 旭日圖", "📊 柱狀圖", "📋 資料表"]
)

# ---- 折線圖：各平台搜尋結果價格走勢 + 平均線 ----
with tab_line:
    fig = px.line(
        df,
        x="序號",
        y="價格",
        color="平台",
        color_discrete_map=cmap,
        markers=True,
        hover_data={"品名": True, "頁數": True, "序號": False},
        title=f"「{kw}」售價比較（依搜尋結果順序）",
    )
    positions = ["top left", "bottom right"]
    for i, p in enumerate(present):
        fig.add_hline(
            y=stats.loc[p, "平均價格"],
            line_dash="dash",
            line_color=cmap[p],
            annotation_text=f"{p} 平均 {stats.loc[p, '平均價格']:,.0f}",
            annotation_position=positions[i % 2],
        )
    fig.update_layout(xaxis_title="商品序號（各平台搜尋結果順序）", yaxis_title="價格 (元)", height=550)
    st.plotly_chart(fig)

# ---- 圓餅圖：各平台並排 ----
with tab_pie:
    dim = st.radio("分類依據", ["價格區間", "品牌"], horizontal=True)
    for col, p in zip(st.columns(len(present)), present):
        sub = df[df["平台"] == p]
        pie_df = sub[dim].value_counts().rename_axis(dim).reset_index(name="商品數量")
        fig = px.pie(
            pie_df,
            names=dim,
            values="商品數量",
            title=f"{p}：{dim}分布",
            category_orders={dim: PRICE_LABELS} if dim == "價格區間" else None,
            hole=0.3,
        )
        fig.update_traces(textinfo="label+percent", sort=(dim != "價格區間"))
        fig.update_layout(height=500)
        col.plotly_chart(fig)
    st.caption("品牌取自品名中【】或開頭[]內的文字；無標示者為「未標示品牌」，前 N 名之外為「其他品牌」。")

# ---- 旭日圖：平台 → 價格區間 → 品牌 ----
with tab_sun:
    sun_df = df.copy()
    sun_df["_p"] = sun_df["平台"].map({p: i for i, p in enumerate(PLATFORMS)})
    sun_df["_r"] = sun_df["價格區間"].map({l: i for i, l in enumerate(PRICE_LABELS)})
    sun_df = sun_df.sort_values(["_p", "_r"]).drop(columns=["_p", "_r"])
    fig = px.sunburst(
        sun_df,
        path=["平台", "價格區間", "品牌"],
        color="平台",
        color_discrete_map=cmap,
        title=f"「{kw}」平台 → 價格區間 → 品牌",
    )
    fig.update_traces(textinfo="label+value", insidetextorientation="radial", sort=False)
    fig.update_layout(height=700)
    st.plotly_chart(fig)

# ---- 柱狀圖 ----
with tab_bar:
    # 1) 價格區間商品數
    cnt = df.groupby(["平台", "價格區間"]).size().reset_index(name="商品數量")
    fig = px.bar(
        cnt,
        x="價格區間",
        y="商品數量",
        color="平台",
        barmode="group",
        color_discrete_map=cmap,
        category_orders={"價格區間": PRICE_LABELS},
        text_auto=True,
        title="各價格區間商品數量",
    )
    fig.update_layout(height=450)
    st.plotly_chart(fig)

    # 2) 價格統計指標
    long = (
        stats[["平均價格", "中位數", "最高價格", "最低價格"]]
        .reset_index()
        .melt(id_vars="平台", var_name="指標", value_name="價格")
    )
    fig = px.bar(
        long,
        x="指標",
        y="價格",
        color="平台",
        barmode="group",
        color_discrete_map=cmap,
        text_auto=",.0f",
        title="價格統計比較",
    )
    fig.update_layout(height=450, yaxis_title="價格 (元)")
    st.plotly_chart(fig)

    # 3) 品牌商品數
    b = df.groupby(["品牌", "平台"]).size().reset_index(name="商品數量")
    order = b.groupby("品牌")["商品數量"].sum().sort_values(ascending=False).index.tolist()
    fig = px.bar(
        b,
        x="品牌",
        y="商品數量",
        color="平台",
        barmode="group",
        color_discrete_map=cmap,
        category_orders={"品牌": order},
        text_auto=True,
        title=f"前 {top_n} 大品牌商品數量",
    )
    fig.update_layout(height=450)
    st.plotly_chart(fig)

    st.dataframe(stats.round(0).astype(int))

# ---- 資料表 / 下載 ----
with tab_data:
    show = df[["平台", "頁數", "序號", "品名", "價格", "價格區間", "品牌"]]
    st.dataframe(show, height=500)
    st.download_button(
        "下載 CSV",
        data=show.to_csv(index=False).encode("utf-8-sig"),
        file_name=f"{datetime.now():%Y%m%d}_PCHOME_MOMO_{kw}.csv",
        mime="text/csv",
    )
