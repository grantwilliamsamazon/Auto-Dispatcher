from db import init_supabase
sb = init_supabase()
res = sb.table('fleet').select('*').in_('van_number', [37, 39, 24, 25]).execute()
for v in res.data:
    print(f"Van {v.get('van_number')}: status={v.get('status')}")
