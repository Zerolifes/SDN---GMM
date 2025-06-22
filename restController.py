from ryu.app.wsgi import ControllerBase, route
from webob import Response
import json
import threading

URL_FLOWMOD = '/flowmod'
URL_FLOWSTATS = '/flowstats/{dpid}'
URL_PORTSTATS = '/portstats/{dpid}'
URL_TABLESTATS = '/tablestats/{dpid}'


class SwitchRestController(ControllerBase):
    def __init__(self, req, link, data, **config):
        super().__init__(req, link, data, **config)
        self.switch_app = data['switch_app']

    @route('flowmod', URL_FLOWMOD, methods=['POST'])
    def flowmod_handler(self, req, **kwargs):
        try:
            # 1. Lấy dữ liệu từ request
            body = req.json_body
            dpid = int(body['dpid'])
            command = body.get('command', 'add')  # add, modify, delete
            strict = body.get('strict', False)
            match_fields = body.get('match', {})
            actions_spec = body.get('actions', [])
            priority = int(body.get('priority', 0))

            dp = self.switch_app.datapaths.get(dpid)
            if not dp:
                return Response(status=404, content_type='application/json; charset=utf-8',
                                body=json.dumps({'error': f"Switch {dpid} not found"}))

            ofp = dp.ofproto
            parser = dp.ofproto_parser

            # 2. Map lệnh
            if command == 'add':
                ofp_cmd = ofp.OFPFC_ADD
            elif command == 'modify':
                ofp_cmd = ofp.OFPFC_MODIFY_STRICT if strict else ofp.OFPFC_MODIFY
            elif command == 'delete':
                ofp_cmd = ofp.OFPFC_DELETE_STRICT if strict else ofp.OFPFC_DELETE
            else:
                return Response(status=400, content_type='application/json; charset=utf-8',
                                body=json.dumps({'error': f"Invalid command: {command}"}))

            # 3. Tạo match
            match = parser.OFPMatch(**match_fields)

            # 4. Tạo actions
            actions = []
            for act in actions_spec:
                atype = act.get('type')
                if atype == 'output':
                    actions.append(parser.OFPActionOutput(int(act['port'])))
                elif atype == 'flood':
                    actions.append(parser.OFPActionOutput(ofp.OFPP_FLOOD))
                elif atype == 'all':
                    actions.append(parser.OFPActionOutput(ofp.OFPP_ALL))
                elif atype == 'set_field':
                    actions.append(parser.OFPActionSetField(**{act['field']: act['value']}))
                elif atype == 'drop':
                    actions = []
                    break  # Không thêm gì ⇒ drop

            instructions = []
            if actions and ofp_cmd != ofp.OFPFC_DELETE and ofp_cmd != ofp.OFPFC_DELETE_STRICT:
                instructions = [parser.OFPInstructionActions(ofp.OFPIT_APPLY_ACTIONS, actions)]

            # 5. Tạo FlowMod
            mod_kwargs = dict(
                datapath=dp,
                command=ofp_cmd,
                priority=priority,
                match=match,
                instructions=instructions
            )

            # Thêm out_port & out_group nếu là lệnh delete
            if ofp_cmd in (ofp.OFPFC_DELETE, ofp.OFPFC_DELETE_STRICT):
                mod_kwargs['out_port'] = ofp.OFPP_ANY
                mod_kwargs['out_group'] = ofp.OFPG_ANY

            mod = parser.OFPFlowMod(**mod_kwargs)
            dp.send_msg(mod)

            return Response(content_type='application/json; charset=utf-8',
                            body=json.dumps({
                                'status': 'ok',
                                'dpid': dpid,
                                'command': command,
                                'strict': strict,
                                'match': match_fields,
                                'actions': actions_spec
                            }, indent=2))
        except Exception as e:
            return Response(status=500, content_type='application/json; charset=utf-8',
                            body=json.dumps({'error': str(e)}))

    @route('flowstats', URL_FLOWSTATS, methods=['POST'])
    def get_flow_stats(self, req, **kwargs):
        try:
            # 1. Lấy dữ liệu
            body = req.json_body
            dpid = int(body['dpid'])
            dp = self.switch_app.datapaths.get(dpid)
            if not dp:
                return Response(status=404, content_type='application/json; charset=utf-8',
                                body=json.dumps({'error': f"Switch {dpid} not found"}))

            # 2. Gửi FlowStatsRequest
            ofp = dp.ofproto
            parser = dp.ofproto_parser
            req_msg = parser.OFPFlowStatsRequest(dp)
            dp.send_msg(req_msg)

            # 3. Chờ phản hồi
            event = threading.Event()
            self.switch_app._waiting_reply[dpid] = {'event': event, 'data': None}
            if not event.wait(timeout=2):
                return Response(status=504, content_type='application/json; charset=utf-8',
                                body=json.dumps({'error': 'Timeout waiting for reply'}))

            stats = self.switch_app._waiting_reply[dpid]['data']

            # 4. Trả kết quả
            return Response(content_type='application/json; charset=utf-8',
                            body=json.dumps({'dpid': dpid, 'flow_stats': stats}, indent=2))

        except Exception as e:
            return Response(status=500, content_type='application/json; charset=utf-8',
                            body=json.dumps({'error': str(e)}))

    @route('portstats', URL_PORTSTATS, methods=['POST'])
    def get_port_stats(self, req, **kwargs):
        try:
            body = req.json_body
            dpid = int(body['dpid'])
            dp = self.switch_app.datapaths.get(dpid)
            if not dp:
                return Response(status=404, content_type='application/json; charset=utf-8',
                                body=json.dumps({'error': f"Switch {dpid} not found"}))

            parser = dp.ofproto_parser
            req_msg = parser.OFPPortStatsRequest(dp, 0, dp.ofproto.OFPP_ANY)
            dp.send_msg(req_msg)

            event = threading.Event()
            self.switch_app._waiting_reply[dpid] = {'event': event, 'data': None}
            if not event.wait(timeout=2):
                return Response(status=504, content_type='application/json; charset=utf-8',
                                body=json.dumps({'error': 'Timeout waiting for reply'}))

            stats = self.switch_app._waiting_reply[dpid]['data']
            return Response(content_type='application/json; charset=utf-8',
                            body=json.dumps({'dpid': dpid, 'port_stats': stats}, indent=2))

        except Exception as e:
            return Response(status=500, content_type='application/json; charset=utf-8',
                            body=json.dumps({'error': str(e)}))
        
    @route('tablestats', URL_TABLESTATS, methods=['POST'])
    def get_table_stats(self, req, **kwargs):
        try:
            body = req.json_body
            dpid = int(body['dpid'])
            dp = self.switch_app.datapaths.get(dpid)
            if not dp:
                return Response(status=404, content_type='application/json; charset=utf-8',
                                body=json.dumps({'error': f"Switch {dpid} not found"}))

            parser = dp.ofproto_parser
            req_msg = parser.OFPTableStatsRequest(dp)
            dp.send_msg(req_msg)

            event = threading.Event()
            self.switch_app._waiting_reply[dpid] = {'event': event, 'data': None}
            if not event.wait(timeout=2):
                return Response(status=504, content_type='application/json; charset=utf-8',
                                body=json.dumps({'error': 'Timeout waiting for reply'}))

            stats = self.switch_app._waiting_reply[dpid]['data']
            return Response(content_type='application/json; charset=utf-8',
                            body=json.dumps({'dpid': dpid, 'table_stats': stats}, indent=2))

        except Exception as e:
            return Response(status=500, content_type='application/json; charset=utf-8',
                            body=json.dumps({'error': str(e)}))
