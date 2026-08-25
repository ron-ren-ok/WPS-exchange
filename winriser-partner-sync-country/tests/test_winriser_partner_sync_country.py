import importlib.util,unittest
from datetime import date
from pathlib import Path
p=Path(__file__).resolve().parents[1]/"src"/"winriser_partner_sync_country.py";spec=importlib.util.spec_from_file_location("sync",p);sync=importlib.util.module_from_spec(spec);spec.loader.exec_module(sync)
class R:
 def __init__(self,t):self.text=t;self.url="https://trk.entiretrack.com/trackingassistant/viewdailyinstallinfo.aspx"
 def raise_for_status(self):pass
class S:
 def get(self,u,timeout):return R('''<form><input type="hidden" name="__VIEWSTATE" value="s"><select name="ctl00$ContentPlaceHolder1$ddSource"></select><select name="ctl00$ContentPlaceHolder1$dddate"></select><input name="ctl00$ContentPlaceHolder1$btnExport" value="Export Excel"></form>''')
 def post(self,u,data,timeout):self.data=data;return R("<table></table>")
class T(unittest.TestCase):
 def test_export(self):
  s=S();self.assertEqual(sync.fetch_country_export(s),"<table></table>");self.assertEqual(s.data["ctl00$ContentPlaceHolder1$ddSource"],"0")
 def test_parse_and_plan(self):
  html='''<table><tr><td>Date</td><td>Source</td><td>Campaign</td><td>Publisher</td><td>Country</td><td>Country Code</td><td>Install Count</td><td>PPI</td></tr><tr><td>08/24/2026</td><td>wnrwpsofc_exchange</td><td>a</td><td></td><td>Italy</td><td>it</td><td>205</td><td>102.5</td></tr><tr><td>08/24/2026</td><td>wnrwpsofc_exchange</td><td>b</td><td></td><td>Italy</td><td>IT</td><td>5</td><td>2.5</td></tr></table>'''
  source=sync.parse_country_report(html,date(2026,8,24));self.assertEqual(source[(date(2026,8,24),"IT","换量弹窗")],{"new_users":210,"blood_volume":105})
  h=["日期","合作方","国家代码","运营位","新增","血量"];u,a,o=sync.plan_writes(h,{},source,False);self.assertEqual((u,o,a[0]["国家代码"]),([],[],"IT"))
if __name__=="__main__":unittest.main()
