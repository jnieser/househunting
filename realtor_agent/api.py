#!/usr/bin/env python
from fastapi import FastAPI, HTTPException, Form
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from datetime import date
from pathlib import Path
import pandas as pd
import requests
import os
import json

# ──────────────────────────────────────────────────────────────────────────────
# Configuration
# ------------------------------------------------------------------------------

ZILLOW_API_KEY = os.getenv("ZILLOW_API_KEY", "93d3672f56mshd2395b93c497f7bp18bd81jsn9fce492a472a")
REGION_POLYGON_MAP = {
    "south_bay": "-122.0391782 37.3736352, -122.0221832 37.3651435, -122.0105527 37.3586118, "
                 "-121.9963486 37.3535791, -121.9694627 37.353144, -121.9703847 37.3606217, "
                 "-121.9654699 37.3693281, -121.9962624 37.3703931, -122.0196293 37.3753218, "
                 "-122.013429 37.3927996, -122.0309814 37.3969251, -122.0391782 37.3736352",
    "bandao":     "-122.2955382 37.5290471, -122.3054946 37.5208785, -122.2476447 37.4593133, "
                 "-122.2255004 37.4708947, -122.2955382 37.5290471"
}
ZILLOW_URL      = "https://zillow-com1.p.rapidapi.com/propertyByPolygon"
HEADERS         = {"x-rapidapi-key": ZILLOW_API_KEY, "x-rapidapi-host": "zillow-com1.p.rapidapi.com"}
DATA_PATH       = Path("data"); DATA_PATH.mkdir(exist_ok=True)

# ──────────────────────────────────────────────────────────────────────────────
# FastAPI app
# ------------------------------------------------------------------------------

app = FastAPI(title="Real-Estate Dashboard")

class Filter(BaseModel):
    min_price:     float = 1_700_000
    max_price:     float = 2_000_000
    min_lot_area:  float = 4_000
    max_days_on_zillow: int = 30

latest_csv: Path = None   # will hold path to most-recent run


# ──────────────────────────────────────────────────────────────────────────────
# Core scraping / filtering logic
# ------------------------------------------------------------------------------

def fetch_region_df(region: str, polygon: str) -> pd.DataFrame:
    q = {"polygon": polygon, "status_type": "ForSale", "home_type": "Houses"}
    res = requests.get(ZILLOW_URL, headers=HEADERS, params=q, timeout=30)
    res.raise_for_status()
    df = pd.json_normalize(res.json()["props"])
    df["region"] = region
    return df


def run_analysis(filt: Filter) -> pd.DataFrame:
    # 1 ┄ collect all regions
    dfs = [fetch_region_df(r, poly) for r, poly in REGION_POLYGON_MAP.items()]
    df  = pd.concat(dfs, ignore_index=True)

    # 2 ┄ derive & filter
    df["price_per_sqft"] = df["price"] / df["livingArea"]
    keep = (
        df.price.between(filt.min_price, filt.max_price)
        & (df.lotAreaValue > filt.min_lot_area)
        & (df.daysOnZillow < filt.max_days_on_zillow)
    )
    cols = ["address", "region", "price", "livingArea", "lotAreaValue",
            "bathrooms", "bedrooms", "daysOnZillow", "price_per_sqft"]
    return df.sort_values("daysOnZillow").loc[keep, cols]


# ──────────────────────────────────────────────────────────────────────────────
# API Endpoints
# ------------------------------------------------------------------------------

@app.post("/run-analysis", response_class=JSONResponse)
async def run_analysis_endpoint(filt: Filter):
    """Trigger a fresh scrape + filter, save to CSV, return rows."""
    global latest_csv
    try:
        df = run_analysis(filt)
        today = date.today().isoformat()
        latest_csv = DATA_PATH / f"real_results_{today}.csv"
        df.to_csv(latest_csv, index=False)
        return json.loads(df.to_json(orient="records"))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/data", response_class=JSONResponse)
async def get_latest_data():
    """Return rows from the latest CSV (if it exists)."""
    if latest_csv and latest_csv.exists():
        df = pd.read_csv(latest_csv)
        return json.loads(df.to_json(orient="records"))
    raise HTTPException(status_code=404, detail="Run the analysis first.")


# ──────────────────────────────────────────────────────────────────────────────
# Front-End (pure HTML + Vanilla JS)
# ------------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    return """
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8" />
  <title>Real-Estate Dashboard</title>
  <script src="https://cdn.jsdelivr.net/npm/vanilla-datatables@latest/dist/vanilla-dataTables.min.js"></script>
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/vanilla-datatables@latest/dist/vanilla-dataTables.min.css">
  <style>
    body{font-family: system-ui, sans-serif; margin:2rem; color:#333}
    h1{margin-bottom:1rem}
    button{padding:0.6rem 1.2rem;font-size:1rem;border:none;border-radius:6px;background:#2563eb;color:#fff;cursor:pointer}
    button:disabled{opacity:0.5;cursor:not-allowed}
    #status{margin:1rem 0}
    table{width:100%;border-collapse:collapse}
    th,td{padding:0.5rem;border:1px solid #e5e7eb}
    th{background:#f3f4f6}
  </style>
</head>
<body>
  <h1>🏠 Real-Estate Dashboard</h1>

  <button id="runBtn" onclick="runAnalysis()">Run Analysis</button>
  <span id="status"></span>

  <table id="tbl" style="display:none">
    <thead>
      <tr>
        <th>Address</th><th>Region</th><th>Price</th><th>Living&nbsp;Area</th>
        <th>Lot&nbsp;Area</th><th>Baths</th><th>Beds</th>
        <th>Days&nbsp;on&nbsp;Zillow</th><th>$/sqft</th>
      </tr>
    </thead>
    <tbody></tbody>
  </table>

<script>
let table, runBtn = document.getElementById('runBtn'),
    statusEl = document.getElementById('status'),
    tbl     = document.getElementById('tbl');

async function runAnalysis(){
  runBtn.disabled = true;  statusEl.textContent = "Running… (this may take ~15-30 s)";
  try{
    const resp  = await fetch('/run-analysis', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({})});
    if(!resp.ok) throw new Error( (await resp.json()).detail );
    const data = await resp.json();
    statusEl.textContent = `Success – ${data.length} rows`;
    populateTable(data);
  }catch(e){
    statusEl.textContent = "Error: " + e.message;
  }finally{
    runBtn.disabled = false;
  }
}

function populateTable(rows){
  const tbody = tbl.querySelector('tbody');
  tbody.innerHTML = '';
  rows.forEach(r=>{
    tbody.insertAdjacentHTML('beforeend', `
      <tr>
        <td>${r.address}</td><td>${r.region}</td><td>${fmt(r.price)}</td>
        <td>${fmt(r.livingArea)}</td><td>${fmt(r.lotAreaValue)}</td>
        <td>${r.bathrooms}</td><td>${r.bedrooms}</td>
        <td>${r.daysOnZillow}</td><td>${fmt(r.price_per_sqft)}</td>
      </tr>`);
  });
  tbl.style.display='table';
  if(table){table.destroy();}
  table = new DataTable("#tbl",{perPage:25,searchable:true,sortable:true});
}

function fmt(x){return Number(x).toLocaleString();}
</script>
</body>
</html>
"""

import uvicorn
import argparse
import sys


# ---- bottom of the file ------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("realtor_agent.api:app", host="0.0.0.0", port=8000, reload=True)
