import random
import threading
import time
from mininet.net import Mininet
from mininet.node import RemoteController
from topo import CustomTopology
from mininet.log import setLogLevel
from mininet.node import OVSSwitch
from mininet.cli import CLI

def client_behavior(client, servers, duration, mode='normal'):
    start_time = time.time()
    while time.time() - start_time < duration:
        if mode == 'normal':
            dst = random.choice(servers)
            method = random.choice(['ping', 'tcp', 'udp', 'none'])
            packet_size = random.randint(64, 512)
            bandwidth = random.randint(100, 500)
            delay = random.uniform(0.5, 2.0)
        else:  # mode='warning'
            dst = servers[0]
            method = random.choice(['tcp', 'udp'])
            packet_size = random.randint(4096, 16384)
            bandwidth = random.randint(10000, 50000)
            delay = random.uniform(0.05, 0.2)

        time.sleep(delay)

        if method == 'none':
            print(f"[{client.name}] No action this round")
            continue

        try:
            if method == 'ping':
                print(f"[{client.name}] ping {dst} with size {packet_size}")
                result = client.cmd(f'ping -c 1 -s {packet_size} {dst}')
                print(f"[{client.name}] ping result: {result.strip()}")

            elif method == 'tcp':
                print(f"[{client.name}] TCP to {dst} with size {packet_size}")
                result = client.cmd(f'iperf -c {dst} -p 5001 -n {packet_size*1024} -l {packet_size} > /dev/null')
                print(f"[{client.name}] TCP result: {result.strip() if result else 'Success'}")

            elif method == 'udp':
                print(f"[{client.name}] UDP to {dst} with size {packet_size}, bandwidth {bandwidth}K")
                result = client.cmd(f'iperf -c {dst} -u -p 5002 -l {packet_size} -b {bandwidth}K -t 0.1')
                print(f"[{client.name}] UDP result: {result.strip()}")
        except Exception as e:
            print(f"[{client.name}] Error: {e}")

def start_servers(net):
    print("==> Starting iperf servers on h1–h3")
    for i in range(1, 4):
        h = net.get(f'h{i}')
        h.cmd('iperf -s -p 5001 > /dev/null 2>&1 &')
        h.cmd('iperf -s -u -p 5002 > /dev/null 2>&1 &')
    time.sleep(2)

def simulate_traffic(net, normal_duration=30, warning_duration=60, normal_duration_2=30):
    print("==> Start iperf servers")
    start_servers(net)

    servers = ['10.0.0.1', '10.0.0.2', '10.0.0.3']
    clients = [net.get(f'h{i}') for i in range(4, 10)]

    # Giai đoạn normal 1
    print(f"==> Starting normal traffic for {normal_duration} seconds")
    threads = []
    for client in clients:
        t = threading.Thread(target=client_behavior, args=(client, servers, normal_duration, 'normal'))
        t.start()
        threads.append(t)
    for t in threads:
        t.join()

    # Giai đoạn warning (DDoS)
    print(f"==> Starting warning traffic for {warning_duration} seconds")
    threads = []
    for client in clients:
        t = threading.Thread(target=client_behavior, args=(client, servers, warning_duration, 'warning'))
        t.start()
        threads.append(t)
    for t in threads:
        t.join()

    # Giai đoạn normal 2
    print(f"==> Starting normal traffic again for {normal_duration_2} seconds")
    threads = []
    for client in clients:
        t = threading.Thread(target=client_behavior, args=(client, servers, normal_duration_2, 'normal'))
        t.start()
        threads.append(t)
    for t in threads:
        t.join()

def main():
    setLogLevel('info')
    topo = CustomTopology()
    net = Mininet(
        topo=topo,
        controller=lambda name: RemoteController(name, ip='127.0.0.1', port=6653),
        switch=OVSSwitch,
        autoSetMacs=True,
        autoStaticArp=True
    )

    net.start()
    simulate_traffic(net, normal_duration=60, warning_duration=20, normal_duration_2=20)
    CLI(net)
    net.stop()

if __name__ == '__main__':
    main()