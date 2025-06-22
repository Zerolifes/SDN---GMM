from ryu.base import app_manager
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, set_ev_cls
from ryu.controller import ofp_event
from ryu.ofproto import ofproto_v1_3
from ryu.app.wsgi import WSGIApplication
from restController import SwitchRestController

import requests
import json

APP_DOMAIN = 'http://127.0.0.1:5000'
EP_CONNECT = f'{APP_DOMAIN}/switch'


class SwitchManager(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]
    _CONTEXTS = {'wsgi': WSGIApplication}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.datapaths = {}
        self._waiting_reply = {}
        wsgi = kwargs['wsgi']
        wsgi.register(SwitchRestController, {'switch_app': self})

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        dp = ev.msg.datapath
        dpid = dp.id
        self.datapaths[dpid] = dp

        payload = {'dpid': dpid}
        try:
            res = requests.post(EP_CONNECT, json=payload)
            status = (res.json().get('status', 'failed'), res.status_code)
        except Exception as e:
            status = f"Failed: {e}"
        print(f"Switch {dpid} connected, notify server: {status}")

        ofp = dp.ofproto
        parser = dp.ofproto_parser

        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofp.OFPP_FLOOD)]
        inst = [parser.OFPInstructionActions(ofp.OFPIT_APPLY_ACTIONS, actions)]

        mod = parser.OFPFlowMod(
            datapath=dp,
            priority=0,
            match=match,
            instructions=inst
        )

        dp.send_msg(mod)
        print(f"[+] Default FLOOD flow installed on switch {dpid}")

    @set_ev_cls(ofp_event.EventOFPFlowStatsReply, MAIN_DISPATCHER)
    def flow_stats_reply_handler(self, ev):
        dpid = ev.msg.datapath.id
        body = ev.msg.body

        stats = [{
            'priority': stat.priority,
            'match': str(stat.match),
            'actions': [str(a) for a in stat.instructions],
            'packet_count': stat.packet_count,
            'byte_count': stat.byte_count
        } for stat in body]

        wait = self._waiting_reply.get(dpid)
        if wait:
            wait['data'] = stats
            wait['event'].set()

        print(json.dumps({
            "event": "FlowStatsReply",
            "dpid": dpid,
            "count": len(stats)
        }, indent=2))
        
    @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    def port_stats_reply_handler(self, ev):
        dpid = ev.msg.datapath.id
        body = ev.msg.body

        stats = [{
            'port_no': stat.port_no,
            'rx_packets': stat.rx_packets,
            'tx_packets': stat.tx_packets,
            'rx_bytes': stat.rx_bytes,
            'tx_bytes': stat.tx_bytes,
            'rx_errors': stat.rx_errors,
            'tx_errors': stat.tx_errors,
            'collisions': stat.collisions
        } for stat in body]

        wait = self._waiting_reply.get(dpid)
        if wait:
            wait['data'] = stats
            wait['event'].set()

        print(json.dumps({
            "event": "PortStatsReply",
            "dpid": dpid,
            "count": len(stats)
        }, indent=2))

    @set_ev_cls(ofp_event.EventOFPTableStatsReply, MAIN_DISPATCHER)
    def table_stats_reply_handler(self, ev):
        dpid = ev.msg.datapath.id
        body = ev.msg.body

        stats = [{
            'table_id': stat.table_id,
            'active_count': stat.active_count,
            'lookup_count': stat.lookup_count,
            'matched_count': stat.matched_count
        } for stat in body]

        wait = self._waiting_reply.get(dpid)
        if wait:
            wait['data'] = stats
            wait['event'].set()

        print(json.dumps({
            "event": "TableStatsReply",
            "dpid": dpid,
            "count": len(stats)
        }, indent=2))