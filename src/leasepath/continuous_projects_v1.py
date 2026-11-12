"""Six development projects with eight cumulative, verifiable user turns.

Only the current turn is released by the controller. This module belongs to
the fixture builder/independent checker, never to an Agent tool subprocess.
All projects are locally authored capability pilots, not public benchmarks.
"""
import csv
import json
from pathlib import Path
import random
import sqlite3


def turn(request, artifact, key, requirement):
    return dict(request=request, artifact=artifact, key=key, requirement=requirement)


def projects():
    inventory = [
        ('available(stock, sku)', 'Return stock.get(sku, 0), without mutating stock.'),
        ('reserve(stock, sku, qty)', 'Return a new stock dictionary after subtracting qty. Require a nonnegative integer qty and sufficient available stock; raise ValueError otherwise. Missing sku starts at zero. Do not mutate stock.'),
        ('release(stock, sku, qty)', 'Return a new stock dictionary adding nonnegative integer qty to sku; raise ValueError for invalid qty. Do not mutate stock.'),
        ('low_stock(stock, threshold)', 'Return sorted SKU names whose quantities are strictly below threshold.'),
        ('stock_value(stock, prices)', 'Return total quantity times price, treating missing prices as zero. Do not round.'),
        ('merge_deliveries(stock, deliveries)', 'Return a new stock dictionary adding each {sku, qty} delivery. Reject negative or noninteger qty with ValueError; never mutate inputs.'),
        ('fulfill_order(stock, order)', 'order is a SKU-to-quantity dictionary. Validate all quantities as nonnegative integers and sufficient stock, then return the updated copy. Raise ValueError without changing inputs on failure.'),
        ('summarize(stock, prices, threshold)', 'Return {units: sum of quantities, value: stock_value, low: low_stock}. Preserve all earlier functions and avoid mutation.'),
    ]
    result = [dict(id='continuous_inventory', family='inventory_repository',
        description='Maintain a small warehouse inventory Python library through changing requirements.',
        tools=['list_files','read_file','search_files','write_file','run_python','run_checks'],
        turns=[turn(f'In inventory.py implement {name}. {spec} Keep all previous behavior. Inspect the project as needed, implement and check it, then deliver.', 'inventory.py', name.split('(')[0], spec) for name,spec in inventory])]
    result.append(dict(id='continuous_billing', family='billing_analysis',
        description='Keep a cumulative billing report backed by invoices.csv and billing.sqlite.',
        tools=['list_files','read_file','search_files','write_file','run_python','query_sql','run_checks'],
        turns=[turn(f'Update report.json, preserving earlier fields. {req} The local database has table invoices; amounts are integer cents. Inspect actual records and validate your result.', 'report.json',key,req)
        for key,req in [
            ('invoice_count','Add invoice_count: the number of invoices.'),
            ('gross_cents','Add gross_cents: sum of amount_cents over all invoices.'),
            ('paid_cents','Add paid_cents: sum of amount_cents with status paid.'),
            ('open_cents','Add open_cents: sum with status open.'),
            ('by_region_cents','Add by_region_cents: object mapping every region to its total amount_cents.'),
            ('overdue_ids','Add overdue_ids: sorted integer IDs where status is open and due_day < 20; equality is not overdue.'),
            ('paid_by_region_cents','Add paid_by_region_cents: every region mapped to paid cents, including zero.'),
            ('reconciliation','Add reconciliation: {accounted_cents: paid plus open plus void cents, balanced: whether accounted equals gross}. Keep all earlier fields.')]]))
    result.append(dict(id='continuous_logs', family='service_diagnostics',
        description='Diagnose a local service incident using config, service notes and recorded logs.',
        tools=['list_files','read_file','search_files','write_file','run_python','run_checks'],
        turns=[turn(f'{req} Preserve prior fixes and report fields. Use the actual service files and logs.', artifact,key,req)
        for artifact,key,req in [
            ('config.json','port','Find the documented health port in service.md and fix config.json port.'),
            ('incident.json','error_counts','Create incident.json error_counts mapping each distinct ERROR code to its occurrence count in service.log.'),
            ('config.json','timeout_ms','Set timeout_ms to the smallest multiple of 100 that is at least the maximum logged latency_ms. Keep the other settings.'),
            ('incident.json','slow_request_ids','Add slow_request_ids: sorted request IDs with latency_ms > 500, including all log levels.'),
            ('config.json','retry_limit','Use the documented retry policy in service.md to fix retry_limit.'),
            ('incident.json','first_error_request','Add first_error_request: the request_id on the earliest ERROR line in file order.'),
            ('config.json','log_level','Set log_level to the documented production level in service.md.'),
            ('incident.json','summary','Add summary: {requests: total log lines, errors: ERROR lines, max_latency_ms: maximum latency}.')]]))
    result.append(dict(id='continuous_api', family='catalog_integration',
        description='Integrate a local catalog HTTP service and maintain a checked integration artifact.',
        tools=['list_files','read_file','search_files','write_file','http_request','run_python','run_checks'],
        turns=[turn(f'Update integration.json and preserve earlier fields. {req} Read api.md for routes and schema; use the real HTTP tool for server data.', 'integration.json',key,req)
        for key,req in [
            ('healthy','Add healthy: whether the health endpoint reports status ok.'),
            ('product_ids','Add product_ids: sorted product IDs returned by the catalog.'),
            ('active_product_ids','Add active_product_ids: sorted IDs of active catalog products.'),
            ('catalog_value_cents','Add catalog_value_cents: sum of price_cents times stock across all catalog products.'),
            ('order_count','Add order_count: count of orders from the orders endpoint.'),
            ('paid_order_ids','Add paid_order_ids: sorted IDs of orders whose status is paid.'),
            ('customer_names','Add customer_names: object mapping string customer IDs to names from the customers endpoint.'),
            ('paid_revenue_cents','Add paid_revenue_cents: sum of qty times catalog price_cents for all lines of paid orders. Join product_id; keep previous fields.')]]))
    result.append(dict(id='continuous_experiment', family='measurement_experiment',
        description='Build and rerun a reproducible sensor-analysis script over real local measurements.',
        tools=['list_files','read_file','search_files','write_file','run_python','run_experiment','run_checks'],
        turns=[turn(f'Extend analysis.py so executing it reads measurements.csv and writes metrics.json. {req} Preserve earlier metric computations, run the script via run_experiment, and validate the saved results. Use Python standard library.', 'metrics.json',key,req)
        for key,req in [
            ('count','Add count: the number of data rows.'),
            ('mean_y','Add mean_y: arithmetic mean of y over all rows.'),
            ('variance_y','Add variance_y: population variance of y over all rows.'),
            ('mean_by_sensor','Add mean_by_sensor: object mapping sensor ID to mean y.'),
            ('slope','Add slope: ordinary least squares slope predicting y from x with an intercept, using all rows.'),
            ('intercept','Add intercept: mean_y minus slope times mean_x.'),
            ('mse','Add mse: mean squared residual of that fitted line over all rows.'),
            ('valid_count','Add valid_count: number of rows where quality equals good. Other metrics still use all rows.')]]))
    result.append(dict(id='continuous_documents', family='release_document_audit',
        description='Cross-check release documentation and keep a cumulative structured handoff.',
        tools=['list_files','read_file','search_files','write_file','run_python','run_checks'],
        turns=[turn(f'Update handoff.json while preserving earlier fields. {req} Read the actual documents; prefer the later dated policy if versions differ.', 'handoff.json',key,req)
        for key,req in [
            ('release','Add release: the release ID in release.md.'),
            ('owners','Add owners: component-to-person object from owners.md.'),
            ('minimum_python','Add minimum_python: minimum supported Python version string from the latest policy document.'),
            ('required_checks','Add required_checks: sorted check names required by the latest policy.'),
            ('open_issue_ids','Add open_issue_ids: sorted IDs of issues whose status is open in issues.json.'),
            ('blocking_issue_ids','Add blocking_issue_ids: sorted open issue IDs whose severity is blocking.'),
            ('ready','Add ready: true only if there are no open blocking issues.'),
            ('audit','Add audit: {issue_count: all issues, open_count: open issues, policy_date: date of the latest policy}.')]]))
    for project in result:
        project.update(split='development', source_group=project['family']+'_authored_fixture_v1', rounds=8)
    return result


def initialize(project, destination):
    root=Path(destination);root.mkdir(parents=True,exist_ok=False)
    def put(name,text):
        (root/name).write_text(text)
    def document(name,value):
        put(name,json.dumps(value,indent=2)+'\n')
    put('README.md',project['description']+'\nWork only on the current requested change. Keep prior deliverables correct.\n')
    if project['id']=='continuous_inventory':
        put('inventory.py','"""Warehouse inventory operations. Quantities and prices are nonnegative."""\n\ndef available(stock, sku):\n    return stock[sku]\n')
        document('sample_stock.json',{'bolts':12,'nuts':4,'washers':0})
    elif project['id']=='continuous_billing':
        rows=[dict(id=i,region=['east','west','north'][i%3],status=['paid','open','void','paid'][i%4],amount_cents=125*i+37,due_day=5+i%24) for i in range(1,37)]
        with (root/'invoices.csv').open('w') as stream:
            writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
        with sqlite3.connect(root/'billing.sqlite') as db:
            db.execute('CREATE TABLE invoices(id INTEGER, region TEXT, status TEXT, amount_cents INTEGER, due_day INTEGER)')
            db.executemany('INSERT INTO invoices VALUES(:id,:region,:status,:amount_cents,:due_day)',rows)
        document('report.json',{})
    elif project['id']=='continuous_logs':
        put('service.md','Health port: 8087\nProduction log level: WARNING\nRetry policy: retry_limit must be 3.\nTimeouts are integer milliseconds.\n')
        document('config.json',{'port':80,'timeout_ms':100,'retry_limit':0,'log_level':'DEBUG'})
        rows=[dict(request_id=f'req-{i:03d}',level='ERROR' if i%4==0 else 'INFO',code=('E_DB' if i%8==0 else 'E_TIMEOUT') if i%4==0 else 'OK',latency_ms=73+(i*89)%1100) for i in range(1,29)]
        put('service.log',''.join(json.dumps(row)+'\n' for row in rows));document('incident.json',{})
    elif project['id']=='continuous_api':
        put('api.md','GET /health -> {status}\nGET /products -> {products: [{id,active,price_cents,stock}]}\nGET /orders -> {orders: [{id,status,customer_id,lines:[{product_id,qty}]}]}\nGET /customers -> {customers:[{id,name}]}\nUse http_request with a relative path; the tool connects to this project local service.\n')
        document('integration.json',{})
    elif project['id']=='continuous_experiment':
        rng=random.Random(901)
        with (root/'measurements.csv').open('w') as stream:
            writer=csv.writer(stream);writer.writerow(['sensor','x','y','quality'])
            for i in range(12000):
                x=(i%401-200)/10
                writer.writerow(['s'+str(i%3),x,round(2.75*x+4+rng.uniform(-.5,.5),6),'bad' if i%17==0 else 'good'])
        put('analysis.py','"""Read measurements.csv and save derived metrics.json here."""\nimport csv, json\n')
    elif project['id']=='continuous_documents':
        put('release.md','Release: warehouse-2026.11\nTarget: staging deployment. Consult latest policy for readiness requirements.\n')
        put('owners.md','Component | Owner\napi | Chen\nworker | Noor\nreports | Mira\n')
        put('policy-2026-10-01.md','Policy date: 2026-10-01\nMinimum Python: 3.11\nRequired checks: unit, lint\n')
        put('policy-2026-11-01.md','Policy date: 2026-11-01\nMinimum Python: 3.13\nRequired checks: unit, lint, integration, schema\nReadiness requires no open blocking issues.\n')
        document('issues.json',[{'id':'I01','status':'closed','severity':'blocking'},{'id':'I02','status':'open','severity':'minor'},{'id':'I03','status':'open','severity':'blocking'},{'id':'I04','status':'closed','severity':'minor'}])
        document('handoff.json',{})
    else:
        raise ValueError('unregistered project')
    return root


def api_payloads():
    return {'/health':{'status':'ok'},
        '/products':{'products':[{'id':11,'active':True,'price_cents':350,'stock':7},{'id':12,'active':False,'price_cents':900,'stock':2},{'id':14,'active':True,'price_cents':125,'stock':20}]},
        '/orders':{'orders':[{'id':101,'status':'paid','customer_id':1,'lines':[{'product_id':11,'qty':2},{'product_id':14,'qty':4}]},{'id':102,'status':'open','customer_id':2,'lines':[{'product_id':12,'qty':1}]},{'id':103,'status':'paid','customer_id':2,'lines':[{'product_id':14,'qty':3}]}]},
        '/customers':{'customers':[{'id':1,'name':'Aster'},{'id':2,'name':'Birch'}]}}
