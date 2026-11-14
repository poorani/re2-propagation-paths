import sys
import numpy as np
import pandas as pd
sys.path.insert(0, sys.argv[1])
from reference_path import build_reference_path

rng = np.random.default_rng(0)
inject = 1000
rows = []
sid = 0
for i in range(4000):
    t = rng.uniform(0, 2000)
    after = t >= inject
    sid += 1; fe = f"s{sid}"
    rows.append(dict(spanID=fe, parentSpanID="none", serviceName="frontendservice",
                     startTimeMillis=t*1000, duration=100, statusCode=0, operationName="HTTP GET /"))
    kind = i % 3
    if kind == 0:  # frontend -> checkout -> payment
        sid += 1; ck = f"s{sid}"
        rows.append(dict(spanID=ck, parentSpanID=fe, serviceName="checkoutservice", operationName="hipstershop.CheckoutService/PlaceOrder",
                         startTimeMillis=t*1000, duration=(200 if after else 90)+rng.normal(0,5),
                         statusCode=0))
        sid += 1
        rows.append(dict(spanID=f"s{sid}", parentSpanID=ck, serviceName="paymentservice", operationName="hipstershop.PaymentService/Charge",
                         startTimeMillis=t*1000, duration=50+rng.normal(0,3), statusCode=0))
    elif kind == 1:  # frontend -> cart (unaffected)
        sid += 1
        rows.append(dict(spanID=f"s{sid}", parentSpanID=fe, serviceName="cartservice", operationName="hipstershop.CartService/GetCart",
                         startTimeMillis=t*1000, duration=40+rng.normal(0,3), statusCode=0))
    else:  # frontend -> health (noise)
        sid += 1
        rows.append(dict(spanID=f"s{sid}", parentSpanID=fe, serviceName="health", operationName="grpc.health.v1.Health/Check",
                         startTimeMillis=t*1000, duration=5, statusCode=0))
    # client-only call: shippingservice records no span of its own
    sid += 1
    rows.append(dict(spanID=f"s{sid}", parentSpanID=fe, serviceName="frontendservice",
                     operationName="/hipstershop.ShippingService/GetQuote",
                     startTimeMillis=t*1000, duration=30+rng.normal(0,2), statusCode=0))
tr = pd.DataFrame(rows)
edges, paths = build_reference_path(tr, inject, "checkoutservice")
print(edges.to_string(index=False))
print("paths:", paths)
assert paths == [["checkoutservice", "frontendservice"]], paths
assert ("frontendservice","shippingservice") in set(zip(edges.caller, edges.callee))
assert len(edges)==4, len(edges)
print("OK")

# entry-point fault: auth is called only from outside (no traced caller)
rows = []
for i in range(2000):
    t = rng.uniform(0, 2000)
    rows.append(dict(spanID=f"a{i}", parentSpanID=f"ext{i}", serviceName="ts-auth-service",
                     operationName="POST /api/v1/users/login", startTimeMillis=t*1000,
                     duration=(500 if t >= inject else 100) + rng.normal(0, 5),
                     statusCode=pd.NA))
    rows.append(dict(spanID=f"b{i}", parentSpanID=f"x{i}", serviceName="ts-station-service",
                     operationName="GET", startTimeMillis=t*1000,
                     duration=20 + rng.normal(0, 2), statusCode=pd.NA))
    rows.append(dict(spanID=f"c{i}", parentSpanID=f"b{i}", serviceName="ts-train-service",
                     operationName="GET", startTimeMillis=t*1000,
                     duration=10 + rng.normal(0, 1), statusCode=pd.NA))
tr2 = pd.DataFrame(rows)
edges2, paths2 = build_reference_path(tr2, inject, "ts-auth-service")
print(edges2.attrs["entry"].to_string(index=False))
print("paths:", paths2)
assert paths2 == [["ts-auth-service"]], paths2
assert not edges2.abnormal.any()
print("OK entry")

# amplification: root slows only 1.3x per call, caller fans out and slows a lot;
# a noisy edge drifts up during normal time and should NOT count
rows = []
for i in range(3000):
    t = rng.uniform(0, 2000)
    after = t >= inject
    rows.append(dict(spanID=f"tr{i}", parentSpanID=f"ext{i}", serviceName="ts-travel-service",
                     operationName="GET", startTimeMillis=t*1000,
                     duration=(1000 if after else 300) + rng.normal(0, 20), statusCode=pd.NA))
    rows.append(dict(spanID=f"tn{i}", parentSpanID=f"tr{i}", serviceName="ts-train-service",
                     operationName="GET", startTimeMillis=t*1000,
                     duration=(1.3 if after else 1.0) * 4400 + rng.normal(0, 100), statusCode=pd.NA))
    rows.append(dict(spanID=f"nz{i}", parentSpanID=f"tr{i}", serviceName="ts-noisy-service",
                     operationName="GET", startTimeMillis=t*1000,
                     duration=100 * (1 + t / 1000) + rng.normal(0, 3), statusCode=pd.NA))
tr3 = pd.DataFrame(rows)
edges3, paths3 = build_reference_path(tr3, inject, "ts-train-service")
print(edges3[["caller", "callee", "slowdown_x", "drift_x", "abnormal", "onset"]].to_string(index=False))
print("paths:", paths3)
assert paths3 == [["ts-train-service", "ts-travel-service"]], paths3
assert not edges3[edges3.callee == "ts-noisy-service"].abnormal.any()
print("OK amplification")

# orphan spans: currencyservice spans lose their parent record mid-request;
# they must NOT make currencyservice look like an entry point
rows = []
for i in range(2000):
    t = rng.uniform(0, 2000)
    rows.append(dict(traceID=f"T{i}", spanID=f"fe{i}", parentSpanID="", serviceName="frontendservice",
                     operationName="GET /", startTimeMillis=t*1000, startTime=t*1e6,
                     duration=200, statusCode=0))
    rows.append(dict(traceID=f"T{i}", spanID=f"cu{i}", parentSpanID=f"lost{i}", serviceName="currencyservice",
                     operationName="hipstershop.CurrencyService/Convert", startTimeMillis=t*1000 + 1,
                     startTime=t*1e6 + 1000, duration=(5 if t >= inject else 50), statusCode=0))
tr4 = pd.DataFrame(rows)
edges4, paths4 = build_reference_path(tr4, inject, "currencyservice")
ent4 = edges4.attrs["entry"]
print(ent4.to_string(index=False)); print("paths:", paths4)
assert list(ent4.service) == ["frontendservice"], list(ent4.service)
assert paths4 == [], paths4
print("OK orphans")

# split traces: currencyservice spans sit alone in single-span traces with an
# empty parent (as in real RE2-OB data); a real entry (auth-like) starts a
# multi-span trace. Only the multi-span one is an entry point.
rows = []
for i in range(2000):
    t = rng.uniform(0, 2000)
    rows.append(dict(traceID=f"A{i}", spanID=f"au{i}", parentSpanID=pd.NA, serviceName="ts-auth-service",
                     operationName="POST /login", startTimeMillis=t*1000, startTime=t*1e6,
                     duration=(400 if t >= inject else 100) + rng.normal(0, 5), statusCode=pd.NA))
    rows.append(dict(traceID=f"A{i}", spanID=f"db{i}", parentSpanID=f"au{i}", serviceName="ts-auth-service",
                     operationName="UserRepository.find", startTimeMillis=t*1000 + 1, startTime=t*1e6 + 500,
                     duration=20, statusCode=pd.NA))
    rows.append(dict(traceID=f"S{i}", spanID=f"cu{i}", parentSpanID=pd.NA, serviceName="currencyservice",
                     operationName="Convert", startTimeMillis=t*1000, startTime=t*1e6,
                     duration=(5 if t >= inject else 50), statusCode=0))
tr5 = pd.DataFrame(rows)
_, paths5 = build_reference_path(tr5, inject, "ts-auth-service")
e5 = _.attrs["entry"]
print(e5.to_string(index=False)); print("paths:", paths5)
assert list(e5.service) == ["ts-auth-service"], list(e5.service)
assert paths5 == [["ts-auth-service"]], paths5
print("OK split traces")

# explicit entry: checkout starts its own multi-span traces (split traces),
# but with entry="frontendservice" only the frontend counts
rows = []
for i in range(2000):
    t = rng.uniform(0, 2000)
    rows.append(dict(traceID=f"F{i}", spanID=f"fe{i}", parentSpanID=pd.NA, serviceName="frontendservice",
                     operationName="GET /", startTimeMillis=t*1000, startTime=t*1e6,
                     duration=200, statusCode=0))
    rows.append(dict(traceID=f"F{i}", spanID=f"fc{i}", parentSpanID=f"fe{i}", serviceName="frontendservice",
                     operationName="hipstershop.CheckoutService/PlaceOrder", startTimeMillis=t*1000, startTime=t*1e6 + 10,
                     duration=150, statusCode=0))
    rows.append(dict(traceID=f"C{i}", spanID=f"ck{i}", parentSpanID=pd.NA, serviceName="checkoutservice",
                     operationName="PlaceOrder", startTimeMillis=t*1000, startTime=t*1e6 + 20,
                     duration=(900 if t >= inject else 100), statusCode=0))
    rows.append(dict(traceID=f"C{i}", spanID=f"cc{i}", parentSpanID=f"ck{i}", serviceName="checkoutservice",
                     operationName="hipstershop.CurrencyService/Convert", startTimeMillis=t*1000, startTime=t*1e6 + 30,
                     duration=10, statusCode=0))
tr6 = pd.DataFrame(rows)
auto, _ = build_reference_path(tr6, inject, "checkoutservice")
assert "checkoutservice" in set(auto.attrs["entry"].service)        # the problem
fixed, paths6 = build_reference_path(tr6, inject, "checkoutservice", entry="frontendservice")
print(fixed.attrs["entry"].to_string(index=False)); print("paths:", paths6)
assert list(fixed.attrs["entry"].service) == ["frontendservice"]
assert [["checkoutservice"]] not in [paths6[:1]]
print("OK explicit entry")

# fail-fast: an out-of-memory productcatalog rejects calls quickly (0.4x),
# while its caller recommendation gets much slower (retries, timeouts)
rows = []
for i in range(3000):
    t = rng.uniform(0, 2000)
    after = t >= inject
    rows.append(dict(traceID=f"R{i}", spanID=f"fe{i}", parentSpanID=pd.NA, serviceName="frontendservice",
                     operationName="GET /", startTimeMillis=t*1000, startTime=t*1e6,
                     duration=(3000 if after else 300) + rng.normal(0, 10), statusCode=0))
    rows.append(dict(traceID=f"R{i}", spanID=f"rc{i}", parentSpanID=f"fe{i}", serviceName="recommendationservice",
                     operationName="ListRecommendations", startTimeMillis=t*1000, startTime=t*1e6 + 10,
                     duration=(2500 if after else 100) + rng.normal(0, 10), statusCode=0))
    rows.append(dict(traceID=f"R{i}", spanID=f"pc{i}", parentSpanID=f"rc{i}", serviceName="productcatalogservice",
                     operationName="ListProducts", startTimeMillis=t*1000, startTime=t*1e6 + 20,
                     duration=(6 if after else 15) + rng.normal(0, 1), statusCode=0))
tr7 = pd.DataFrame(rows)
edges7, paths7 = build_reference_path(tr7, inject, "productcatalogservice", entry="frontendservice")
print(edges7[["caller", "callee", "slowdown_x", "direction", "abnormal", "onset"]].to_string(index=False))
print("paths:", paths7)
assert paths7 == [["productcatalogservice", "recommendationservice", "frontendservice"]], paths7
print("OK fail-fast")

# regression guard: normal time drifts slightly FASTER (second half quicker),
# then the fault makes the edge 1.3x slower; that slowdown must still count
rows = []
for i in range(3000):
    t = rng.uniform(0, 2000)
    base = 4400 * (0.75 if 500 <= t < 1000 else 1.0)
    rows.append(dict(spanID=f"tv{i}", parentSpanID=f"ext{i}", serviceName="ts-travel-service",
                     operationName="GET", startTimeMillis=t*1000,
                     duration=(1000 if t >= inject else 300) + rng.normal(0, 20), statusCode=pd.NA))
    rows.append(dict(spanID=f"tt{i}", parentSpanID=f"tv{i}", serviceName="ts-train-service",
                     operationName="GET", startTimeMillis=t*1000,
                     duration=(1.3 * 4400 if t >= inject else base) + rng.normal(0, 100), statusCode=pd.NA))
tr8 = pd.DataFrame(rows)
_, paths8 = build_reference_path(tr8, inject, "ts-train-service")
print("paths:", paths8)
assert paths8 == [["ts-train-service", "ts-travel-service"]], paths8
print("OK direction-specific drift")
