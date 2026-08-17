---
name: cad-partner-sync
description: Maintain and verify the recurring CAD DWG new-user sync into 合作方新增血量.
---

# CAD Partner Sync

Use this procedure for changes to `cad-partner-sync` or `.github/workflows/cad-partner-sync.yml`.

1. Confirm source spreadsheet `1zhFz3d996b1oiD4p4Oc786dPWlriu7gg6g2S6TN83b8`, sheet `DWG新增`, headers `日期 | 运营位 | DWG安装量`.
2. Confirm target spreadsheet `1vSBU84SFoVlXdaczYYAev8mC0PEfjRQyVSv8s2OAGW4`, sheet `合作方新增血量`, headers `日期 | 合作方 | 运营位 | 新增 | 血量`.
3. Map every nonblank source operation dynamically to target `合作方=CAD`; write C only to `新增` and never to `血量`.
4. Treat blank source metrics as unreported and zero as valid. Skip summary rows and rows with blank operations.
5. Default to no overwrite for different nonblank target values. Use manual `allow_overwrite` only after the user explicitly approves corrections.
6. Keep the service-account JSON only in GitHub Secret `GOOGLE_SHEET_SERVICE_ACCOUNT_JSON`; never print or commit it.
7. Run `python -m unittest cad-partner-sync/tests/test_cad_partner_sync.py` and `python -m py_compile cad-partner-sync/src/cad_partner_sync.py` after changes.
8. Before production write validation, inspect exact source/target ranges. Do not manually write production rows unless the user requests a live run.
