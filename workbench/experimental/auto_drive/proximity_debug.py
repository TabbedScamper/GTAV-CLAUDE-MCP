"""Diagnose why the front ray missed a car at 9m. Spawn dead-ahead, then dump the debug ray + all
sensors + the target's bearing relative to the car's CURRENT forward."""
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
    r=send('call_native_by_name',{'name':n,'args':a,'return_type':rt}); return r.get('result') if isinstance(r,dict) else r

p=call('PLAYER_PED_ID',[],'int')
veh=call('GET_VEHICLE_PED_IS_IN',[p,False],'int')
if not veh: print('not in a vehicle — run sensor_validate first to get seated'); raise SystemExit
pos=call('GET_ENTITY_COORDS',[veh,True],'vector3'); fwd=call('GET_ENTITY_FORWARD_VECTOR',[veh],'vector3')
vz=call('GET_ENTITY_HEIGHT_ABOVE_GROUND',[veh],'float')
n=math.hypot(fwd[0],fwd[1]) or 1; fx,fy=fwd[0]/n,fwd[1]/n
print(f'car at {[round(x,1) for x in pos]} fwd {[round(fx,2),round(fy,2)]} height_above_ground {round(vz or 0,2)}')

mh=call('GET_HASH_KEY',['adder'],'int'); call('REQUEST_MODEL',[mh],'void')
for _ in range(25):
    if call('HAS_MODEL_LOADED',[mh],'bool'): break
    time.sleep(0.2)
sp=call('CREATE_VEHICLE',[mh,pos[0]+fx*9,pos[1]+fy*9,pos[2],call('GET_ENTITY_HEADING',[veh],'float'),False,False,False],'int')
call('SET_ENTITY_DYNAMIC',[sp,False],'void')  # keep it put
time.sleep(0.8)
spc=call('GET_ENTITY_COORDS',[sp,True],'vector3')
# bearing of target relative to car forward
dx,dy=spc[0]-pos[0],spc[1]-pos[1]; dn=math.hypot(dx,dy) or 1; dx,dy=dx/dn,dy/dn
align=fx*dx+fy*dy; off_deg=math.degrees(math.acos(max(-1,min(1,align))))
print(f'spawned adder {sp} at {[round(x,1) for x in spc]} dist {dn:.1f}m, {off_deg:.1f}deg off forward, dz {round(spc[2]-pos[2],2)}')

print('\nDEBUG front ray (car present):')
d=send('drive_sense_debug',{})
print('  status',d.get('status'),'hit',d.get('hit'),'entity',d.get('entity'),'(adder handle',sp,')',
      'computed_front_m',d.get('computed_front_m'),'hit_xyz',[round(x,1) if isinstance(x,(int,float)) else x for x in d.get('hit_xyz',[])])
print('\nfull sweep (car present):')
print('  ', send('drive_sense',{}).get('summary'))

# cleanup
alloc=int(send('alloc_cave',{'size':8})['address'],16); send('write',{'address':hex(alloc),'type':'int32','value':sp})
call('SET_ENTITY_AS_MISSION_ENTITY',[sp,True,True],'void'); call('DELETE_VEHICLE',[alloc],'void')
call('SET_MODEL_AS_NO_LONGER_NEEDED',[mh],'void')
print('\ncleaned up:', not call('DOES_ENTITY_EXIST',[sp],'bool'))
