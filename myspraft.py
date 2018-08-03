# coding:utf-8

from ryu.base import app_manager
from ryu.controller import mac_to_port
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.mac import haddr_to_bin
from ryu.lib.packet import packet
from ryu.lib.packet import ethernet
from ryu.lib.packet import ether_types
from ryu.lib import mac
from ryu.topology.api import get_switch, get_link, get_host
from ryu.app.wsgi import ControllerBase
from ryu.topology import event, switches

import networkx as nx
import matplotlib.pyplot as plt
from ryu.lib import hub
import socket
from ryu.lib.packet import arp

import sys
from functools import partial
sys.path.append("/home/openlab/openlab/ryu/ryu/app/PySyncObj")
from pysyncobj import SyncObj, replicated
import time


#partners = ["192.168.1.141:5001" , "192.168.1.167:5001"]
partners = ["192.168.2.8:5001" , "192.168.2.167:5001"]

item = []

class ProjectController(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(ProjectController, self).__init__(*args, **kwargs)
        self.mac_to_port = {}
	#self.raftObj = TestObj('192.168.1.162:5001' , partners)
	self.raftObj = TestObj('192.168.2.9:5001', partners)
        self.topology_api_app = self
        self.localNet = nx.DiGraph()
	self.localNet_old = nx.DiGraph()
        self.nodes = {}
        self.links = {}
        self.no_of_nodes = 0
        self.no_of_links = 0
	self.dps=[]
        self.i = 0
	self.controller = 4
	self.isMoreDomain = 0
	self.dict_paths = {}
	self.threads.append(
            hub.spawn(self._topo_local_sync, 5))
	self.is_active = True
	listen_thr = hub.spawn(self.listenToEvent)
	self.initFile()
        print "**********ProjectController __init__"

    def printG(self):
        G = self.raftObj.net
        print "G"
        print "nodes", G.nodes()  # 输出全部的节点： [1, 2, 3]
        print "edges", G.edges()  # 输出全部的边：[(2, 3)]
        print "number_of_edges", G.number_of_edges()  # 输出边的数量：1
	print ''
        #for e in G.edges():
            #print G.get_edge_data(e[0], e[1])

    # Handy function that lists all attributes in the given object
    def ls(self, obj):
        print("\n".join([x for x in dir(obj) if x[0] != "_"]))

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        print "\n-----------switch_features_handler is called"

        msg = ev.msg
        print 'OFPSwitchFeatures received: datapath_id=0x%016x n_buffers=%d n_tables=%d auxiliary_id=%d capabilities=0x%08x' % (
            msg.datapath_id, msg.n_buffers, msg.n_tables, msg.auxiliary_id, msg.capabilities)

        datapath = ev.msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER, ofproto.OFPCML_NO_BUFFER)]
        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]
        mod = datapath.ofproto_parser.OFPFlowMod(
            datapath=datapath, match=match, cookie=0,
            command=ofproto.OFPFC_ADD, idle_timeout=0, hard_timeout=0, priority=0, instructions=inst)
        datapath.send_msg(mod)
        print "switch_features_handler is over"
	try:
	    self.dps.append(datapath)
	except Exception, e:
	    print str(e)
	
    def add_flow(self, datapath, in_port, dst, actions, buffer_id=None):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        match = datapath.ofproto_parser.OFPMatch(in_port=in_port, eth_dst=dst)
        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]
	if buffer_id:
            mod = datapath.ofproto_parser.OFPFlowMod(
                datapath=datapath, buffer_id=buffer_id, match=match, cookie=0,
                command=ofproto.OFPFC_ADD, idle_timeout=0, hard_timeout=0,
                priority=ofproto.OFP_DEFAULT_PRIORITY, instructions=inst)
	else:
            mod = datapath.ofproto_parser.OFPFlowMod(
                datapath=datapath, match=match, cookie=0,
                command=ofproto.OFPFC_ADD, idle_timeout=0, hard_timeout=0,
                priority=ofproto.OFP_DEFAULT_PRIORITY, instructions=inst)
        datapath.send_msg(mod)

    def adddict(self,targetDict,src,dst,val):
	if src in targetDict:
	    targetDict[src].update({dst:val})
	else:
	    targetDict.update({src:{dst:val}})

    def isIndict(self,targetDict,src,dst):
	target = False
	if src in targetDict:
	    if dst in targetDict[src]:
		target = True
	return target

    #mac learning
    def mac_learning(self, datapath, src, in_port):
        self.mac_to_port.setdefault((datapath,datapath.id), {})
        # learn a mac address to avoid FLOOD next time.
        if src in self.mac_to_port[(datapath,datapath.id)]:
            if in_port != self.mac_to_port[(datapath,datapath.id)][src]:
                return False
        else:
            self.mac_to_port[(datapath,datapath.id)][src] = in_port
            return True

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def _packet_in_handler(self, ev):
        # print "**********_packet_in_handler"
        msg = ev.msg
        datapath = msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        in_port = msg.match['in_port']
        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocol(ethernet.ethernet)

        if eth.ethertype == ether_types.ETH_TYPE_LLDP:
            # ignore lldp packet
            return
        if eth.ethertype == ether_types.ETH_TYPE_IPV6:
            # ignore ipv6 packet
	    #print 'IPV6 pkt droped'
            return

        dst = eth.dst
        src = eth.src
        dpid = datapath.id
        #self.mac_to_port.setdefault(dpid, {})
	#if src not in self.mac_to_port[dpid]:
	    #self.mac_to_port[dpid][src] = in_port
	self.mac_learning(datapath, src, in_port)    

        if dst in self.raftObj.net:

            G= self.raftObj.net

            #G[3][5]['weight'] = 10

	    if self.isIndict(self.dict_paths,src,dst) == False:
                path = nx.shortest_path(G, src, dst, weight="weight")
		self.adddict(self.dict_paths,src,dst,path)
		tmpPath = list(reversed(path))
		self.adddict(self.dict_paths,dst,src,tmpPath)
	    else:
		path = self.dict_paths[src][dst]

            next = path[path.index(dpid) + 1]
            out_port = G[dpid][next]['port']

	else:
            if self.mac_learning(datapath, src, in_port) is False:
		out_port = ofproto.OFPPC_NO_RECV
                return
		#print drop clc ARP pkt
	    else:
                out_port = ofproto.OFPP_FLOOD

        #actions = [datapath.ofproto_parser.OFPActionOutput(out_port)]
	actions = [parser.OFPActionOutput(out_port)]

        # install a flow to avoid packet_in next time
        if out_port != ofproto.OFPP_FLOOD:
	    match = parser.OFPMatch(in_port=in_port, eth_dst=dst)
	    if msg.buffer_id != ofproto.OFP_NO_BUFFER:
		self.add_flow(datapath, in_port, dst, actions, msg.buffer_id)
		#return
	    else:
                self.add_flow(datapath, in_port, dst, actions)
	    
        data = None
        if msg.buffer_id == ofproto.OFP_NO_BUFFER:
            data = msg.data
        out = datapath.ofproto_parser.OFPPacketOut(
            datapath=datapath, buffer_id=msg.buffer_id, in_port=in_port,
            actions=actions)
        datapath.send_msg(out)

    def _topo_local_sync(self, interval):
        while self.is_active:
	    switch_list = get_switch(self.topology_api_app, None)

            switches = [switch.dp.id for switch in switch_list]
	    strTopoMessage = ''
	    #print '++++++++++++++++++++++++++++++++++++++++++++++++++++++++'
            if len(switches) > 0:
                for sw in switches:
		    tmpStr = str(sw) + '#'
		    strTopoMessage = strTopoMessage + tmpStr
		    #print sw
	    #print '++++++++++++++++++++++++++++++++++++++++++++++++++++++++'

	    strTopoMessage = strTopoMessage.rstrip('#')
	    if strTopoMessage is not '':
	        strTopoMessage = strTopoMessage + '$'

	    links_list = get_link(self.topology_api_app, None)
	    #print '++++++++++++++++++++++++++++++++++++++++++++++++++++++++'
	    tmpStr = ''
            if len(links_list) > 0:
		for link in links_list:
		    linkStr=str(link.src.dpid)+','+str(link.dst.dpid)+','+str(link.src.port_no)+','+str(link.dst.port_no)+','+str(0)+')'
		    tmpStr = tmpStr + linkStr
		#links = [(link.src.dpid, link.dst.dpid, link.src.port_no) for link in links_list]
        	#links = [(link.dst.dpid, link.src.dpid, link.dst.port_no) for link in links_list]
		tmpStr = tmpStr.rstrip(')')
		tmpStr = tmpStr + '$'
		strTopoMessage = strTopoMessage + tmpStr
		#print tmpStr
	    #print '++++++++++++++++++++++++++++++++++++++++++++++++++++++++'
	    tmpStr = ''
	    hosts_list = get_host(self.topology_api_app, None)
	    for host in hosts_list:
		#print host
		#print ''
		#print host.port.hw_addr
		for host_i in hosts_list:
		    if host != host_i:
			if abs(host.port.dpid) == abs(host_i.port.dpid) and host.port.port_no == host_i.port.port_no:
			    host_i.port.dpid = -abs(host_i.port.dpid)
			    #hosts_list.remove(host_i)
			    if host.port.dpid > 0:
				host.port.dpid = -abs(host.port.dpid)
	    
	    for host in hosts_list:
		#print host.port.dpid
		#print host.mac
		#print ' || port' + str(host.port.port_no)		
		if host.port.dpid > 0:
		    #print 'tesult host'
		    #print host
		    hostStr=str(host.port.dpid)+','+str(host.mac)+','+str(host.port.port_no)+'#'
		    tmpStr = tmpStr + hostStr
	    tmpStr = tmpStr.rstrip('#')
	    strTopoMessage = strTopoMessage + tmpStr	    
#	        self.net.add_node(host.mac)
#                self.net.add_edge(host.port.dpid, host.mac, port=host.port.port_no, weight=0)
#                self.net.add_edge(host.mac, host.port.dpid, weight=0)
	    #self.printG()
	    #This is Controller1

	    #get possiable interLinks massage
	    try:
       	        f_rt_switch=open("/home/openlab/openlab/ryu/interLinks.log","r+")	  
	        interLinksStr=str(f_rt_switch.read())
		f_rt_switch.truncate()
	    finally:
	        f_rt_switch.close()
	    if strTopoMessage is not '':		
	        strTopoMessage = strTopoMessage + '$' + interLinksStr

	    strTopoMessage = '1@' + strTopoMessage
	    #self.printG()
	    try:
	        f_rf_switch=open("/home/openlab/openlab/ryu/topoMessage.log","w+")
	        f_rf_switch.write(str(strTopoMessage))
	    finally:
    	        f_rf_switch.close()
	    #self.printG()
            hub.sleep(interval)

    def initFile(self):
	    try:
	        f_rf_switch1=open("/home/openlab/openlab/ryu/topoMessage.log","w+")
		f_rf_switch2=open("/home/openlab/openlab/ryu/interLinks.log","w+")
		f_rf_switch1.truncate()
		f_rf_switch2.truncate()
	    finally:
    	        f_rf_switch1.close()
	    	f_rf_switch2.close()

    @set_ev_cls(event.EventLinkDelete)
    def networkChange_link_deleted(self,ev): 
	print('deleted link: %d<-->%d' % (ev.link.src.dpid,ev.link.dst.dpid))
	self.raftObj.links_delete(ev.link.src.dpid, ev.link.dst.dpid)
	#self.del_linkDeleted(ev.link.src.dpid,ev.link.dst.dpid)     
        #update mac_learning_table 
	#self.update_macLearning(self,ev.link.dst.dpid,ev.link.dst.port_no)
	self.raftObj.updateOtherDomain_flow(ev.link.src.dpid, ev.link.dst.dpid, ev.link.dst.port_no)

    @set_ev_cls(event.EventLinkAdd)
    def networkChange_link_add(self,ev):
	print('added link: %d<-->%d' % (ev.link.src.dpid,ev.link.dst.dpid))
	self.raftObj.links_add(ev.link.src.dpid, ev.link.dst.dpid, ev.link.src.port_no)

    @set_ev_cls(event.EventPortDelete)
    def networkChange_port_deleted(self,ev):
	self.raftObj.getDeletedInterlink(ev.port.dpid,ev.port.port_no)

    def update_macLearning(self,dst_dpid,dst_port):
	dp = self.get_datapath(dst_dpid)
	s_keys = list(self.mac_to_port[(dp,dst_dpid)].keys())
	for src in s_keys:
	    if self.mac_to_port[(dp,dst_dpid)][src] == dst_port:
		try:
		    del self.mac_to_port[(dp,dst_dpid)][src]
		except Exception,e:
		    print str(e)


    def del_linkDeleted(self, src_dpid, dst_dpid):
	#deal path of changed links
	self.isMoreDomain = 0
	for path_list in self.dict_paths:
	    dict_dst = self.dict_paths[path_list]
	    for dict_dst_list in dict_dst:
		list_path = self.dict_paths[path_list][dict_dst_list]
		for i in range(len(list_path) - 3):
		    if list_path[i+1] == src_dpid and list_path[i+2] ==dst_dpid:
			print 'test......'
			#del_action
			src = list_path[0]
			dst = list_path[len(list_path)-1]
			    				
			#update new path
			old_path = list(self.dict_paths[src][dst])
			path = nx.shortest_path(self.raftObj.net, src, dst, weight="weight")
		        self.dict_paths[src][dst] = list(path)
			old_path_tmp = list(old_path)
			new_path_tmp = list(path)
			old_path_tmp.pop()
			new_path_tmp.pop()
			#print type(path)
			while(1):
			    old_element = old_path_tmp[len(old_path_tmp)-2]
			    new_element = new_path_tmp[len(new_path_tmp)-2]
			    if old_element != new_element:
				break
			    else:
				old_path_tmp.pop()
				new_path_tmp.pop()
			while(1):
			    if len(old_path_tmp) >=2 and len(new_path_tmp) >=2:
			        old_element = old_path_tmp[1]
			        new_element = new_path_tmp[1]
			        if old_element == new_element:
				    del(old_path_tmp[0])
				    del(new_path_tmp[0])
				else:
				    break
			    else:
				break
			print old_path_tmp
			print new_path_tmp

			#del switches's old flow table
			for delete_dpid in old_path_tmp:
			    delete_dp = self.get_datapath(delete_dpid)
			    if delete_dp in self.dps:
			        in_port = self.mac_to_port[(delete_dp,delete_dpid)][src]
			        try:
				    [self.remove_flows(delete_dp,n,in_port,dst) for n in [0, 1]]
			        except Exception, e:
				    print str(e)
			    else:
				self.isMoreDomain = 1
			
			#update switches's new flow table
			for update_dpid in new_path_tmp:
			    try:
			        update_dp = self.get_datapath(update_dpid)
			        if update_dp in self.dps:
			            in_port_update = self.mac_to_port[(update_dp,update_dpid)][src]
			            self.update_flow(update_dp,path,in_port_update,dst)
			    except Exception,e:
				print str(e)
			    else:
				self.isMoreDomain = 1	
			
			break

    def get_datapath(self,dpid):
	for dp in self.dps:
	    if dp.id == dpid:
		return dp
	return None

    def update_flow(self, datapath, path, in_port, dst):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
	next = path[path.index(datapath.id) + 1]
        out_port = self.raftObj.net[datapath.id][next]['port']
	actions = [parser.OFPActionOutput(out_port)]
	match = parser.OFPMatch(in_port=in_port, eth_dst=dst)
        self.add_flow(datapath, in_port, dst, actions)
        data = None
        out = datapath.ofproto_parser.OFPPacketOut(
            datapath=datapath,buffer_id=ofproto.OFP_NO_BUFFER,in_port=in_port,
            actions=actions)
	print 'update'
	print path
        datapath.send_msg(out)

    def remove_flows(self, datapath, table_id, inp, dst_mac):
        """Removing matched flow entries."""
        parser = datapath.ofproto_parser
        ofproto = datapath.ofproto
        match = parser.OFPMatch(in_port=inp, eth_dst=dst_mac)
        instructions = []
        flow_mod = self.remove_table_flows(datapath, table_id,
                                        match, instructions)
        datapath.send_msg(flow_mod)
    

    def remove_table_flows(self, datapath, table_id, match, instructions):
        """Create OFP flow mod message to remove flows from table."""
        ofproto = datapath.ofproto
        flow_mod = datapath.ofproto_parser.OFPFlowMod(datapath, 0, 0, table_id,
                                                      ofproto.OFPFC_DELETE, 0, 0,
                                                      1,
                                                      ofproto.OFPCML_NO_BUFFER,
                                                      ofproto.OFPP_ANY,
                                                      ofproto.OFPG_ANY, 0,
                                                      match, instructions)
        return flow_mod

    def listenToEvent(self):
	global item
	while True:
	    time.sleep(0.005)
	    if item is not []:
		for link_changed in item:
		    #print link_changed
		    src_dpid = int(link_changed.split(',')[0])
		    dst_dpid = int(link_changed.split(',')[1])
		    dst_port = int(link_changed.split(',')[2])
		    self.del_linkDeleted(src_dpid, dst_dpid)
		    dst_dp = self.get_datapath(dst_dpid)
		    if dst_dp in self.dps:
		        self.update_macLearning(dst_dpid, dst_port)		
		item = []

class TestObj(SyncObj):

    def __init__(self, selfNodeAddr, otherNodeAddrs):
        super(TestObj, self).__init__(selfNodeAddr, otherNodeAddrs)
        self.net = nx.DiGraph()
	self.__counter = 0
	self.swStr = ''
	self.lkStr = ''
	self.htStr = ''
	self.inlks = ''
	self.interLink_set=[]

    #prase Topology Message string
    def praseTopoStr(self,strTopo):
	mesgSwitches = strTopo.split('$')[0]
	mesgLinks = strTopo.split('$')[1]
	mesgHost = strTopo.split('$')[2]
	mesgInterlinks = strTopo.split('$')[3]
	sw_dpid = []

	print 'Praseing......'
	try:
	    switches = mesgSwitches.split('#')
	    for i_sw in range(len(switches)):
	        switch = int(switches[i_sw])
	        sw_dpid.append(switch)
	        self.net.add_nodes_from([switch])
	except:
	    print 'ErrormesgSwitches:>>>>>'
	    print mesgSwitches

	try:
	    links = mesgLinks.split(')')
	    for i_links in range(len(links)):
	        link_src_dpid = int(links[i_links].split(',')[0])
	        link_dst_dpid = int(links[i_links].split(',')[1])
	        link_src_port = int(links[i_links].split(',')[2])
	        link_dst_port = int(links[i_links].split(',')[3])
	        link_weight = int(links[i_links].split(',')[4])
	        #print link_src_dpid
	        #print link_dst_dpid
	        addlink = [(link_src_dpid,link_dst_dpid,{'port':link_src_port})]
	        self.net.add_edges_from(addlink)
	        addlink = [(link_dst_dpid,link_src_dpid,{'port':link_dst_port})]
	        self.net.add_edges_from(addlink)
	except:
	    print 'ErrormesgLinks:>>>>>'
	    print mesgLinks

	try:
	    hosts = mesgHost.split('#')
	    for i_hosts in range(len(hosts)):
	        host_port_dpid = int(hosts[i_hosts].split(',')[0])
	        host_mac = str(hosts[i_hosts].split(',')[1])
	        host_port = int(hosts[i_hosts].split(',')[2])
	        if host_port_dpid > 0:
	            if host_mac not in self.net:
	                self.net.add_node(host_mac)
	                self.net.add_edge(host_port_dpid,host_mac,port=host_port,weight=0)
	                self.net.add_edge(host_mac,host_port_dpid,weight=0)
	except:
	    print 'ErrormesgHost:>>>>>'
	    print mesgHost
	if mesgInterlinks is not '':
	    if mesgInterlinks[0] == '#':
	        mesgInterlinks.lstrip('#')
	try:
	    if mesgInterlinks is not '':
	        interlinks = mesgInterlinks.split('#')
		self.interLink_set=[]
	        for i_inlinks in range(len(interlinks)):
	            inlink_src_dpid = int(interlinks[i_inlinks].split(',')[0])
	            inlink_dst_dpid = int(interlinks[i_inlinks].split(',')[1])
	            inlink_src_port = int(interlinks[i_inlinks].split(',')[2])
	            inlink_dst_port = int(interlinks[i_inlinks].split(',')[3])
	            inlink_weight = int(interlinks[i_inlinks].split(',')[4])
	            if inlink_src_dpid in sw_dpid and inlink_dst_dpid in sw_dpid:
		        addlink = [(inlink_src_dpid,inlink_dst_dpid,{'port':inlink_src_port})]
			strInterLink = str(inlink_src_dpid)+','+str(inlink_dst_dpid)+','+str(inlink_src_port)+','+str(inlink_dst_port)
			self.interLink_set.append(strInterLink)
	    	        self.net.add_edges_from(addlink)
	    	        addlink = [(inlink_dst_dpid,inlink_src_dpid,{'port':inlink_dst_port})]
	    	        self.net.add_edges_from(addlink)
	except:
	    print 'ErrormesgInterlinks:>>>>>'
	    print mesgInterlinks


    def sendMestoController(self,buf):
	if len(buf) > 12:
	#print 'mess'
	#print buf
	    if buf[len(buf)-1] != '&':
	        buf = buf.rstrip('%')
	        networks = buf.split('%')
	        for i_nt in range(len(networks)):
		    if networks[i_nt] is not '':
		        try:	
			    self.swStr = self.swStr + networks[i_nt].split('$')[0] + '#'
			    self.lkStr = self.lkStr + networks[i_nt].split('$')[1] + ')'
			    self.htStr = self.htStr + networks[i_nt].split('$')[2] + '#'
			    if buf[len(buf)-1] != '$':
			        self.inlks = self.inlks + networks[i_nt].split('$')[3] + '#'
		                #print self.swStr
		        except:
			    print 'deal message Error'
	    else:
		buf = buf.rstrip('&')
		networks = buf.split('%')
	        for i_nt in range(len(networks)):
		    if networks[i_nt] is not '':
			try:
			    self.swStr = self.swStr + networks[i_nt].split('$')[0] + '#'
			    self.lkStr = self.lkStr + networks[i_nt].split('$')[1] + ')'
		   	    self.htStr = self.htStr + networks[i_nt].split('$')[2] + '#'
			    if buf[len(buf)-1] != '$':
			        self.inlks = self.inlks + networks[i_nt].split('$')[3] + '#'
	    		except:
			    print 'deal message Error'
		try:
		    gl_tpMesg=self.swStr.rstrip('#')+'$'+self.lkStr.rstrip(')')+'$'+self.htStr.rstrip('#')+'$'+self.inlks.rstrip('#')
		except:
		    gl_tpMesg = ''
		#if gl_tpMesg[len(gl_tpMesg)-1] == '#' and gl_tpMesg is not '':
		    #gl_tpMesg = gl_tpMesg.rstrip('#')
		#print gl_tpMesg
		if gl_tpMesg is not '':
		    self.net = nx.DiGraph()
		    self.praseTopoStr(gl_tpMesg)
		#self.net.add_edges_from([(4,3,{'port':2})])
		#self.net.add_edges_from([(3,4,{'port':4})])
		self.swStr = ''
		self.lkStr = ''
		self.htStr = ''
		self.inlks = ''
	        self.printG()
	#except:
	    #print 'prase Error'

    @replicated
    def incCounter(self):
	self.__counter += 1
	print self.__counter

    @replicated
    def links_delete(self,target_link_src_dpid,target_link_dst_dpid):
	self.net.remove_edge(target_link_src_dpid, target_link_dst_dpid)
	self.printG()
	self.setSymbol()

    @replicated
    def links_add(self,target_link_src_dpid,target_link_dst_dpid, target_link_src_port):
	addlink = [(target_link_src_dpid,target_link_dst_dpid,{'port':target_link_src_port})]
	self.net.add_edges_from(addlink)
	self.printG()
	self.setSymbol()

    @replicated
    def getDeletedInterlink(self,port_dpid,port):
	if self.interLink_set is not []:
	    global item
	    for inter_links in self.interLink_set:
		src_dpid = int(inter_links.split(',')[0])
		dst_dpid = int(inter_links.split(',')[1])
		src_port = int(inter_links.split(',')[2])
		dst_port = int(inter_links.split(',')[3])
		if port_dpid == src_dpid and port == src_port:
		    try:
		        self.net.remove_edge(src_dpid,dst_dpid)
			itemStr = str(src_dpid)+','+str(dst_dpid) + ',' + str(dst_port)
			item.append(itemStr)
		    except:
			pass
		    self.setSymbol()
		    self.printG()
		elif port_dpid == dst_dpid and port == dst_port:
		    try:
		        self.net.remove_edge(dst_dpid,src_dpid)
			itemStr = str(dst_dpid)+','+str(src_dpid) + ',' + str(src_port)
			item.append(itemStr)
		    except:
			pass
		    self.setSymbol()
		    self.printG()
	        

    @replicated
    def updateOtherDomain_flow(self,src_dpid,dst_dpid,dst_port):
	#self.del_linkDeleted(src_dpid,dst_dpid)
	#self.update_macLearning(dst_dpid,dist_port)
	global item
	itemStr = str(src_dpid)+','+str(dst_dpid) + ',' + str(dst_port)
	item.append(itemStr)
	#pass

    def getGraph(self):
        return self.net

    def printG(self):
        G = self.net
        print "G"
        print "nodes", G.nodes()  # 输出全部的节点： [1, 2, 3]
        print "edges", G.edges()  # 输出全部的边：[(2, 3)]
        print "number_of_edges", G.number_of_edges()  # 输出边的数量：19
	print ''
	print self.interLink_set
        #for e in G.edges():
            #print G.get_edge_data(e[0], e[1])
