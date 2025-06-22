from flask import Flask, request, render_template
import threading
import time
import requests
import numpy as np
from sklearn.mixture import GaussianMixture
from sklearn.preprocessing import StandardScaler
import datetime

app = Flask(__name__)
BASE_URL = "http://127.0.0.1:8080"
DURATION_TRAIN = 20  # Thời gian thu thập mẫu để huấn luyện GMM (giây)
T_SAMPLING = 0.5     # Khoảng thời gian giữa các lần lấy mẫu (giây)
MAX_SAMPLES = 200    # Số mẫu tối đa lưu trữ để cập nhật GMM và scaler

connected_dpids = set()
sampling_enabled = threading.Event()
prev_features = {}
gmm_model = None
scaler = None
sampling_start_time = None
feature_vectors = []
predictions = []
normal_component = None  # Lưu chỉ số thành phần normal

@app.route('/switch', methods=['POST'])
def receive_switch_info():
    data = request.get_json()
    dpid = data.get('dpid')
    if dpid:
        connected_dpids.add(str(dpid))
        print(f"Switch connected: {dpid}")
    return {'status': 'received'}, 200

@app.route('/', methods=['GET', 'POST'])
def index():
    global sampling_enabled
    result = None
    flow_result = None
    selected_dpid = None

    if request.method == 'POST':
        selected_dpid = request.form.get('dpid')
        action = request.form.get('action')

        if action == 'start_sampling':
            sampling_enabled.set()
            return render_template("sampling.html", 
                                 connected_dpids=sorted(connected_dpids),
                                 results=[])

        try:
            if action in ['flowstats', 'portstats', 'tablestats']:
                r = requests.post(f"{BASE_URL}/{action}/{selected_dpid}", json={"dpid": int(selected_dpid)})
                result = r.json()

            elif action == 'flowmod':
                cmd = request.form.get('cmd', 'add')
                in_port = request.form.get('in_port')
                out_port = request.form.get('out_port')
                eth_type = request.form.get('eth_type')
                ip_proto = request.form.get('ip_proto')
                ipv4_src = request.form.get('ipv4_src')
                ipv4_dst = request.form.get('ipv4_dst')
                priority = request.form.get('priority') or 100
                strict = request.form.get('strict') == 'on'

                match = {}
                if in_port: match['in_port'] = int(in_port)
                if eth_type: match['eth_type'] = int(eth_type, 0)
                if ip_proto: match['ip_proto'] = int(ip_proto)
                if ipv4_src: match['ipv4_src'] = ipv4_src
                if ipv4_dst: match['ipv4_dst'] = ipv4_dst

                actions = []
                if out_port:
                    if out_port.upper() == 'FLOOD':
                        actions.append({'type': 'FLOOD'})
                    else:
                        actions.append({'type': 'output', 'port': int(out_port)})

                payload = {
                    "dpid": int(selected_dpid),
                    "command": cmd,
                    "match": match,
                    "actions": actions,
                    "priority": int(priority),
                    "strict": strict
                }

                r = requests.post(f"{BASE_URL}/flowmod", json=payload)
                flow_result = r.json()

        except Exception as e:
            result = {"error": str(e)}

    return render_template("index.html",
                         connected_dpids=sorted(connected_dpids),
                         selected_dpid=selected_dpid,
                         result=result,
                         flow_result=flow_result)

@app.route('/sampling_data')
def sampling_data():
    return {'results': predictions}

def collect_port_stats():
    global prev_features, gmm_model, scaler, sampling_start_time, feature_vectors, predictions, normal_component
    while True:
        time.sleep(T_SAMPLING)

        if not sampling_enabled.is_set():
            continue

        if sampling_start_time is None:
            sampling_start_time = time.time()

        current_features = {}
        all_deltas = []
        current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        for dpid in list(connected_dpids):
            try:
                url = f"{BASE_URL}/portstats/{dpid}"
                res = requests.post(url, json={"dpid": int(dpid)}, timeout=1)
                if res.status_code == 200:
                    data = res.json()
                    for port in data.get("port_stats", []):
                        port_no = port.get("port_no")
                        if port_no == 4294967294:
                            continue
                        key = f"{dpid}-{port_no}"

                        stats = [
                            port.get("rx_packets", 0),
                            port.get("tx_packets", 0),
                            port.get("rx_bytes", 0),
                            port.get("tx_bytes", 0)
                        ]

                        current_features[key] = stats

                        prev = prev_features.get(key, [0, 0, 0, 0])
                        delta = [curr - p for curr, p in zip(stats, prev)]
                        all_deltas.extend(delta)

                else:
                    print(f"[WARN] Cannot get port stats from switch {dpid}")
            except Exception as e:
                print(f"[ERROR] Collecting stats for DPID {dpid}: {e}")

        if all_deltas:
            feature_vectors.append(all_deltas)
            elapsed_time = time.time() - sampling_start_time

            print(f"[DEBUG] Collected sample {len(feature_vectors)} with {len(all_deltas)} features at {current_time}")

            if elapsed_time < DURATION_TRAIN and len(feature_vectors) >= 20:
                try:
                    # Chuẩn hóa và huấn luyện GMM
                    X = np.array(feature_vectors)
                    if np.any(np.isnan(X)) or np.any(np.isinf(X)):
                        print(f"[ERROR] Invalid values in feature vectors: {X}")
                        status = "error"
                    else:
                        scaler = StandardScaler()
                        X_scaled = scaler.fit_transform(X)
                        gmm_model = GaussianMixture(
                            n_components=3,
                            random_state=42,
                            reg_covar=1e-4
                        )
                        gmm_model.fit(X_scaled)
                        # Xác định thành phần normal
                        probs = gmm_model.predict_proba(X_scaled)
                        mean_probs = np.mean(probs, axis=0)
                        normal_component = np.argmax(mean_probs)
                        status = "train"
                        print(f"[TRAIN] GMM trained at {current_time} with {len(feature_vectors)} samples, {X.shape[1]} features, normal_component={normal_component}")
                except Exception as e:
                    status = "error"
                    print(f"[ERROR] GMM training failed: {e}")
            elif elapsed_time >= DURATION_TRAIN and gmm_model is not None and scaler is not None:
                # Chuẩn hóa mẫu mới và dự đoán
                X = np.array([all_deltas])
                if np.any(np.isnan(X)) or np.any(np.isinf(X)):
                    status = "error"
                    print(f"[ERROR] Invalid values in feature vector: {X}")
                else:
                    try:
                        X_scaled = scaler.transform(X)
                        # Dự đoán và lấy xác suất
                        prediction = gmm_model.predict(X_scaled)[0]
                        probs = gmm_model.predict_proba(X_scaled)[0]
                        status = "normal" if prediction == normal_component else "warning"
                        
                        # Cập nhật scaler và GMM với MAX_SAMPLES gần nhất
                        feature_vectors_updated = np.array(feature_vectors[-MAX_SAMPLES:] + [all_deltas])
                        scaler = StandardScaler()
                        X_scaled_updated = scaler.fit_transform(feature_vectors_updated)
                        gmm_model.fit(X_scaled_updated)
                        # Cập nhật lại normal_component
                        probs_updated = gmm_model.predict_proba(X_scaled_updated)
                        mean_probs_updated = np.mean(probs_updated, axis=0)
                        normal_component = np.argmax(mean_probs_updated)
                        print(f"[PREDICT] {current_time} - Vector: {all_deltas} - Status: {status} - Probs: {probs} - normal_component={normal_component}")
                    except Exception as e:
                        status = "error"
                        print(f"[ERROR] GMM prediction/update failed: {e}")
            else:
                status = "collecting"
                print(f"[COLLECT] {current_time} - Vector: {all_deltas}")

            predictions.append({
                'time': current_time,
                'vector': all_deltas,
                'status': status
            })

            # Giới hạn số mẫu lưu trữ
            if len(predictions) > MAX_SAMPLES:
                predictions.pop(0)
            if len(feature_vectors) > MAX_SAMPLES:
                feature_vectors.pop(0)

        prev_features = current_features.copy()

if __name__ == '__main__':
    threading.Thread(target=collect_port_stats, daemon=True).start()
    app.run(host='0.0.0.0', port=5000, debug=True)