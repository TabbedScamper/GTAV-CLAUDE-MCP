"""Safe sensor validation after the 24-byte Vector3 offset fix. NO driving — static reads only.
1) debug dump of the front ray, 2) full 8-ray sweep, 3) spawn a car 9m ahead -> confirm front reads ~9m,
4) dry-run auto_drive decision."""
import socket, struct, json, time, math

def send(c,p=None,t=12):
    pl=json.dumps({'command':c,'params':p or {}}).encode()
    s=socket.socket(); s.settimeout(t); s.connect(('127.0.0.1',27015))
    s.sendall(struct.pack('<I',len(pl))+pl); n=s.recv(4); sz=struct.unpack('<I',n)[0]; b=b''
    while len(b)<sz:
        x=s.recv(min(8192,sz-len(b)))
        if not x: break
        b+=x
    s.close(); return json.loads(b.decode())
def call(n,a,rt='void'):
    r=send('call_native_by_name',{'name':n,'args':a,'return_type':rt})
    return r.get('result') if isinstance(r,dict) else r

# must be in a vehicle for sensors; if on foot, spawn one and get in
p=call('PLAYER_PED_ID',[],'int')
if not call('IS_PED_IN_ANY_VEHICLE',[p,False],'bool'):
    print('on foot -> spawning a car to sit in for the test...')
    pos=call('GET_ENTITY_COORDS',[p,True],'vector3'); h=call('GET_ENTITY_HEADING',[p],'float')
    mh=call('GET_HASH_KEY',['sultan'],'int'); call('REQUEST_MODEL',[mh],'void')
    for _ in range(25):
        if call('HAS_MODEL_LOADED',[mh],'bool'): break
        time.sleep(0.2)
    v=call('CREATE_VEHICLE',[mh,pos[0],pos[1],pos[2],h,False,False,False],'int')
    call('SET_PED_INTO_VEHICLE',[p,v,-1],'void'); call('SET_MODEL_AS_NO_LONGER_NEEDED',[mh],'void')
    time.sleep(0.5)

print('\n[1] DEBUG front-ray buffer dump:')
d=send('drive_sense_debug',{})
if 'Unknown command' in str(d.get('error','')):
    print('   not loaded yet -> press F9, re-run'); raise SystemExit
print('   status',d.get('status'),'hit',d.get('hit'),'entity',d.get('entity'),
      'computed_front_m',d.get('computed_front_m'),'hit_xyz',[round(x,1) if isinstance(x,(int,float)) else x for x in d.get('hit_xyz',[])])

print('\n[2] full 8-ray sweep (static):')
print('   ', send('drive_sense',{}).get('summary'))

print('\n[3] proximity test: spawn a car ~9m ahead, expect front ~9m:')
veh=call('GET_VEHICLE_PED_IS_IN',[p,False],'int')
pos=call('GET_ENTITY_COORDS',[veh,True],'vector3'); fwd=call('GET_ENTITY_FORWARD_VECTOR',[veh],'vector3')
n=math.hypot(fwd[0],fwd[1]) or 1; fx,fy=fwd[0]/n,fwd[1]/n
mh=call('GET_HASH_KEY',['adder'],'int'); call('REQUEST_MODEL',[mh],'void')
for _ in range(25):
    if call('HAS_MODEL_LOADED',[mh],'bool'): break
    time.sleep(0.2)
sp=call('CREATE_VEHICLE',[mh,pos[0]+fx*9,pos[1]+fy*9,pos[2],0.0,False,False,False],'int'); time.sleep(0.7)
spc=call('GET_ENTITY_COORDS',[sp,True],'vector3'); actual=math.dist(pos[:2],spc[:2])
front=send('drive_sense',{}).get('sensors',{}).get('front',{}).get('clear')
print(f'   actual {actual:.1f}m | front sensor {front}m -> {"PASS" if (front and abs(front-actual)<4) else "FAIL"}')
# cleanup spawned car
alloc=int(send('alloc_cave',{'size':8})['address'],16); send('write',{'address':hex(alloc),'type':'int32','value':sp})
call('SET_ENTITY_AS_MISSION_ENTITY',[sp,True,True],'void'); call('DELETE_VEHICLE',[alloc],'void')
call('SET_MODEL_AS_NO_LONGER_NEEDED',[mh],'void')

print('\n[4] dry-run auto_drive decision (no driving):')
print('   cruise:', send('auto_drive',{'mode':'cruise','dry_run':True}).get('summary'))
print('   recover:', send('drive_recover',{}))
