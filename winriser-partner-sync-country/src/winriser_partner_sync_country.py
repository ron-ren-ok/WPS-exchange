import argparse, json, os, re, sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import requests
from bs4 import BeautifulSoup
SHEET_ID="1vSBU84SFoVlXdaczYYAev8mC0PEfjRQyVSv8s2OAGW4"; SHEET_NAME="合作方新增血量分国家"
HEADERS=("日期","合作方","国家代码","运营位","新增","血量"); PARTNER="Winriser"
LOGIN_URL="https://trk.entiretrack.com/trackingassistant/"; REPORT_URL=LOGIN_URL+"viewdailyinstallinfo.aspx"
SOURCE_TO_OPERATION={"wnrwpsofc":"气泡","wnrwpsofc_exchange":"换量弹窗","wnrwps_radar":"文档雷达"}
def parse_day(v):
 t=str(v).strip().split(",",1)[0]
 for f in ("%Y-%m-%d","%Y/%m/%d","%d.%m.%Y","%m/%d/%Y"):
  try:return datetime.strptime(t,f).date()
  except ValueError:pass
 try:
  n=float(t)
  if 20000<=n<=80000:return (datetime(1899,12,30)+timedelta(days=n)).date()
 except ValueError:pass
 raise ValueError(f"unsupported date: {v!r}")
def number(v):
 t=str(v).replace(",","").replace("$","").strip()
 if not re.fullmatch(r"-?\d+(?:\.\d+)?",t):raise ValueError(f"invalid numeric value: {v!r}")
 n=float(t);return int(n) if n.is_integer() else n
def form_data(s):return {e["name"]:e.get("value","") for e in s.select("input[type=hidden][name]")}
def login(session,secret):
 r=session.get(LOGIN_URL,timeout=30);r.raise_for_status();s=BeautifulSoup(r.text,"html.parser")
 p=s.select_one("input[type=password][name]");u=next((x for x in s.select("input[type=text][name]") if x.get("name")),None);b=next((x for x in s.select("input[type=submit][name]") if "login" in x.get("value","").lower()),None)
 if not p or not u or not b:raise RuntimeError("Tracker login form changed")
 d=form_data(s);d.update({u["name"]:"WPS",p["name"]:secret,b["name"]:b.get("value","Login")});r=session.post(r.url,data=d,timeout=30);r.raise_for_status()
 if "dashboard.aspx" not in r.url.lower() and "logout" not in r.text.lower():raise RuntimeError("Tracker login was not accepted")
def fetch_country_export(session):
 r=session.get(REPORT_URL,timeout=30);r.raise_for_status();s=BeautifulSoup(r.text,"html.parser")
 source=s.select_one("select[name='ctl00$ContentPlaceHolder1$ddSource']");day=s.select_one("select[name='ctl00$ContentPlaceHolder1$dddate']")
 export=next((x for x in s.select("input[name],button[name]") if "export" in x.get_text(" ",strip=True).lower() or "export" in x.get("value","").lower()),None)
 if not source or not day or not export:raise RuntimeError("Tracker country export controls changed")
 d=form_data(s);d.update({source["name"]:"0",day["name"]:"3",export["name"]:export.get("value","Export Excel")});r=session.post(REPORT_URL,data=d,timeout=30);r.raise_for_status();return r.text
def parse_country_report(html,cutoff):
 s=BeautifulSoup(html,"html.parser");want=("Date","Source","Campaign","Publisher","Country","Country Code","Install Count","PPI")
 table=next((x for x in s.find_all("table") if x.find("tr") and tuple(c.get_text(" ",strip=True) for c in x.find("tr").find_all(["td","th"]))==want),None)
 if table is None:raise RuntimeError("Tracker country export table headers changed")
 out={}
 for tr in table.find_all("tr")[1:]:
  c=[x.get_text(" ",strip=True) for x in tr.find_all("td")]
  if len(c)!=8:continue
  day,source,_,_,_,country,installs,spend=c;op=SOURCE_TO_OPERATION.get(source.strip().lower().split(" - ",1)[0])
  if not op:continue
  day=parse_day(day)
  if day>cutoff:continue
  country=country.strip().upper()
  if not re.fullmatch(r"[A-Z]{2}",country):raise RuntimeError(f"Tracker returned an invalid country code: {country!r}")
  row=out.setdefault((day,country,op),{"new_users":0,"blood_volume":0});row["new_users"]+=number(installs);row["blood_volume"]+=number(spend)
 if not out:raise RuntimeError("Tracker returned no mapped country-detail rows")
 return out
def col(n):
 out=""
 while True:
  n,r=divmod(n,26);out=chr(65+r)+out
  if n==0:return out
  n-=1
def service(raw):
 from google.oauth2.service_account import Credentials
 from googleapiclient.discovery import build
 return build("sheets","v4",credentials=Credentials.from_service_account_info(json.loads(raw),scopes=["https://www.googleapis.com/auth/spreadsheets"]),cache_discovery=False)
def val(row,i):return row[i] if i<len(row) else ""
def target_rows(api):
 values=api.spreadsheets().values().get(spreadsheetId=SHEET_ID,range=f"'{SHEET_NAME}'!A1:F10000",valueRenderOption="UNFORMATTED_VALUE",dateTimeRenderOption="SERIAL_NUMBER").execute().get("values",[])
 if not values or len(values[0])!=len(set(values[0])) or any(h not in values[0] for h in HEADERS):raise RuntimeError("country-detail target headers are missing or duplicated")
 head=values[0];pos={h:head.index(h) for h in HEADERS};out={}
 for no,row in enumerate(values[1:],2):
  if row and val(row,pos["日期"]):
   key=(parse_day(val(row,pos["日期"])),str(val(row,pos["合作方"])).strip(),str(val(row,pos["国家代码"])).strip().upper(),str(val(row,pos["运营位"])).strip())
   if key in out:raise RuntimeError(f"duplicate country-detail record: {key}")
   out[key]=(no,row)
 return head,out
def plan_writes(head,targets,source,overwrite):
 pos={h:head.index(h) for h in HEADERS};updates=[];appends=[];conflicts=[];overwrites=[]
 for (day,country,op),m in sorted(source.items()):
  found=targets.get((day,PARTNER,country,op))
  if not found:appends.append({"日期":day,"合作方":PARTNER,"国家代码":country,"运营位":op,"新增":m["new_users"],"血量":m["blood_volume"]});continue
  no,row=found
  for h,k in (("新增","new_users"),("血量","blood_volume")):
   old,want=val(row,pos[h]),m[k]
   if old in ("",None) or abs(float(old)-float(want))>1e-9:
    if old not in ("",None) and not overwrite:conflicts.append(f"{day} {country}/{op}/{h}");continue
    updates.append({"range":f"'{SHEET_NAME}'!{col(pos[h])}{no}","values":[[want]]})
    if old not in ("",None):overwrites.append(f"{day} {country}/{op}/{h}")
 if conflicts:raise RuntimeError("refusing to overwrite country-detail conflicts: "+"; ".join(conflicts))
 return updates,appends,overwrites
def append(api,head,records):
 if not records:return
 pos={h:head.index(h) for h in HEADERS};rows=[]
 for record in records:
  row=[""]*len(head)
  for h in HEADERS:row[pos[h]]=record[h].isoformat() if h=="日期" else record[h]
  rows.append(row)
 api.spreadsheets().values().append(spreadsheetId=SHEET_ID,range=f"'{SHEET_NAME}'!A1:{col(len(head)-1)}",valueInputOption="USER_ENTERED",insertDataOption="INSERT_ROWS",body={"majorDimension":"ROWS","values":rows}).execute()
def main():
 p=argparse.ArgumentParser();p.add_argument("--end-date");p.add_argument("--allow-overwrite",action="store_true");a=p.parse_args()
 secret=os.environ.get("WINRISER_LOGIN_SECRET","").strip().strip('"').strip("'");raw=os.environ.get("GOOGLE_SHEET_SERVICE_ACCOUNT_JSON")
 if not secret or not raw:raise RuntimeError("missing required GitHub Actions secret")
 cutoff=parse_day(a.end_date) if a.end_date else datetime.now(ZoneInfo("Asia/Shanghai")).date()-timedelta(days=1)
 with requests.Session() as s:s.headers["User-Agent"]="WPS partner country sync/1.0";login(s,secret);source=parse_country_report(fetch_country_export(s),cutoff)
 api=service(raw);head,targets=target_rows(api);updates,appends,overwrites=plan_writes(head,targets,source,a.allow_overwrite)
 if updates:api.spreadsheets().values().batchUpdate(spreadsheetId=SHEET_ID,body={"valueInputOption":"USER_ENTERED","data":updates}).execute()
 append(api,head,appends);print(json.dumps({"records":len(source),"updated_cells":len(updates),"appended_rows":len(appends),"overwrites":overwrites},ensure_ascii=False))
if __name__=="__main__":
 try:main()
 except (RuntimeError,ValueError,requests.RequestException,json.JSONDecodeError) as exc:print(f"ERROR: {exc}",file=sys.stderr);sys.exit(2)
