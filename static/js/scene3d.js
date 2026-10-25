/**
 * 蜂群仿真 — Three.js 3D 场景引擎
 * P0: 地形/天空/UAV模型/相机 | P1: 高度图/水面/建筑
 * P2: 尾焰/爆炸/雷达锥/链路 | P3: 夜视/军标/画中画
 */
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

class SwarmScene3D {
    constructor(containerId) {
        this.container = document.getElementById(containerId);
        if (!this.container) return this;
        this.W = this.container.clientWidth;
        this.H = this.container.clientHeight;

        // === P0: 基础场景 ===
        this.scene = new THREE.Scene();
        this.scene.background = new THREE.Color(0x1a2b4a);
        this.scene.fog = new THREE.Fog(0x1a2b4a, 4000, 15000);

        this.camera = new THREE.PerspectiveCamera(60, this.W/this.H, 50, 25000);
        this.camera.position.set(5000, 4000, 7000);
        this.camera.lookAt(5000, 5000, 0);

        this.renderer = new THREE.WebGLRenderer({antialias:true});
        this.renderer.setSize(this.W, this.H);
        this.renderer.setPixelRatio(Math.min(window.devicePixelRatio,2));
        this.renderer.shadowMap.enabled = true;
        this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
        this.container.appendChild(this.renderer.domElement);

        // 灯光
        this.sun = new THREE.DirectionalLight(0xfff5e6, 1.5);
        this.sun.position.set(8000, 6000, 2000);
        this.sun.castShadow = true;
        this.sun.shadow.mapSize.set(2048,2048);
        this.sun.shadow.camera.near=100; this.sun.shadow.camera.far=20000;
        this.sun.shadow.camera.left=-6000; this.sun.shadow.camera.right=6000;
        this.sun.shadow.camera.top=6000; this.sun.shadow.camera.bottom=-6000;
        this.scene.add(this.sun);
        this.scene.add(new THREE.AmbientLight(0x334466, 0.6));
        this.hemi = new THREE.HemisphereLight(0x8899cc, 0x334422, 0.4);
        this.scene.add(this.hemi);

        // OrbitControls
        this.controls = new OrbitControls(this.camera, this.renderer.domElement);
        this.controls.target.set(5000,5000,0);
        this.controls.enableDamping=true; this.controls.dampingFactor=0.1;
        this.controls.maxPolarAngle=Math.PI*0.48; this.controls.minDistance=500; this.controls.maxDistance=15000;
        this.controls.update();

        // 对象池
        this.uavMeshes = {}; this.intrMeshes = {}; this.targetMeshes = {};
        this.buildings = []; this.particleSystems = {}; this.radarCones = {};
        this.linkLines = {}; this.labels = {};

        // 状态
        this.lastState = null; this.viewMode='free'; this.followId=null; this.fpvId=null;
        this.nightMode=false; this.irMode=false; this.showRadar=true; this.showLinks=true;

        // === P0: 地形 ===
        this._buildTerrain();
        // === P1: 水面+城市 ===
        this._buildWater();
        this._buildUrbanArea();
        // === P0: 天空 ===
        this._buildSky();

        // 事件
        this._bindEvents();
        this._animLoop();
        return this;
    }

    // ===== P0+P1: 地形 =====
    _buildTerrain() {
        const size=10000, segs=150;
        const geo = new THREE.PlaneGeometry(size, size, segs, segs);
        geo.rotateX(-Math.PI/2);
        const pos = geo.attributes.position;
        const colors = new Float32Array(pos.count*3);

        for (let i=0; i<pos.count; i++) {
            const x=pos.getX(i), y=pos.getY(i);
            // P1: 高度图 — 西北山地 + 东南平原
            let h=0;
            const dx=x/size+0.5, dy=y/size+0.5;
            // 山脉
            if (dx<0.4&&dy<0.5) h=200+Math.sin(dx*12)*Math.cos(dy*8)*180+Math.sin(dx*5+dy*3)*120;
            // 丘陵
            else if (dx<0.6&&dy>0.6) h=50+Math.sin(dx*7)*Math.cos(dy*6)*80;
            // 起伏
            else h=Math.sin(dx*3)*Math.cos(dy*4)*30+Math.sin(dx*7+dy*5)*15;
            pos.setZ(i, h);

            // 地形着色: 低=绿, 中=棕, 高=灰白
            const eh=h/300;
            if (eh<0.15) { colors[i*3]=0.15; colors[i*3+1]=0.35+eh; colors[i*3+2]=0.1; }
            else if (eh<0.4) { colors[i*3]=0.25+eh*0.2; colors[i*3+1]=0.3+eh*0.3; colors[i*3+2]=0.1; }
            else if (eh<0.7) { colors[i*3]=0.4+eh*0.3; colors[i*3+1]=0.25+eh*0.1; colors[i*3+2]=0.1; }
            else { colors[i*3]=0.5+eh*0.3; colors[i*3+1]=0.4+eh*0.2; colors[i*3+2]=0.2+eh*0.3; }
        }
        geo.setAttribute('color', new THREE.BufferAttribute(colors,3));
        geo.computeVertexNormals();

        const mat = new THREE.MeshStandardMaterial({
            vertexColors:true, roughness:0.85, metalness:0.05,
            flatShading: false,
        });
        this.terrain = new THREE.Mesh(geo, mat);
        this.terrain.receiveShadow = true;
        this.scene.add(this.terrain);

        // 网格线
        const gridHelper = new THREE.PolarGridHelper(5000, 40, 20, 64, 0x334455, 0x223344);
        gridHelper.position.y=0.5;
        this.scene.add(gridHelper);
    }

    // ===== P1: 水面 =====
    _buildWater() {
        const geo = new THREE.PlaneGeometry(2500, 2500);
        geo.rotateX(-Math.PI/2);
        const mat = new THREE.MeshStandardMaterial({
            color:0x1a5588, roughness:0.2, metalness:0.6, transparent:true, opacity:0.75
        });
        this.water = new THREE.Mesh(geo, mat);
        this.water.position.set(1500, 0.3, 1500);
        this.scene.add(this.water);
    }

    // ===== P1: 城市建筑群 =====
    _buildUrbanArea() {
        const bx=6800, bz=6500, spread=1200;
        const buildingMat = new THREE.MeshStandardMaterial({roughness:0.6,metalness:0.3});
        for (let i=0; i<80; i++) {
            const w=15+Math.random()*40, d=15+Math.random()*30;
            const h=20+Math.random()*150;
            const geo = new THREE.BoxGeometry(w,h,d);
            const mesh = new THREE.Mesh(geo, buildingMat.clone());
            mesh.material.color.setHSL(0.08+Math.random()*0.05, 0.1, 0.3+Math.random()*0.4);
            mesh.position.set(bx+(Math.random()-0.5)*spread, h/2, bz+(Math.random()-0.5)*spread);
            mesh.castShadow=true; mesh.receiveShadow=true;
            this.scene.add(mesh);
            this.buildings.push(mesh);
        }
    }

    // ===== P0: 天空 =====
    _buildSky() {
        const skyGeo = new THREE.SphereGeometry(8000, 32, 32);
        const skyMat = new THREE.ShaderMaterial({
            uniforms: { topColor:{value:new THREE.Color(0x3377cc)}, bottomColor:{value:new THREE.Color(0x8899bb)}, offset:{value:20}, exponent:{value:0.5} },
            vertexShader: `varying vec3 vWorldPosition; void main(){ vec4 wp=modelMatrix*vec4(position,1.0); vWorldPosition=wp.xyz; gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.0); }`,
            fragmentShader: `uniform vec3 topColor; uniform vec3 bottomColor; uniform float offset; uniform float exponent; varying vec3 vWorldPosition; void main(){ float h=normalize(vWorldPosition+offset).y; gl_FragColor=vec4(mix(bottomColor,topColor,max(pow(max(h,0.0),exponent),0.0)),1.0); }`,
            side:THREE.BackSide
        });
        this.sky = new THREE.Mesh(skyGeo, skyMat);
        this.scene.add(this.sky);
    }

    // ===== P0: 3D UAV模型 (程序化多旋翼) =====
    _createUAVMesh(uav) {
        const group = new THREE.Group();
        const roleColors = {leader:0xa855f7, scout:0x00ff88, striker:0xff4757, relay:0xffd700};
        const color = roleColors[uav.role]||0x888888;

        // 机身
        const bodyGeo = new THREE.CylinderGeometry(3,4,12,8);
        bodyGeo.rotateX(Math.PI/2);
        const bodyMat = new THREE.MeshStandardMaterial({color, roughness:0.3, metalness:0.7});
        const body = new THREE.Mesh(bodyGeo, bodyMat);
        body.castShadow=true;
        group.add(body);

        // 机臂 x4
        for (let i=0; i<4; i++) {
            const armGeo = new THREE.CylinderGeometry(0.5,0.5,18,6);
            armGeo.rotateZ(Math.PI/2);
            const arm = new THREE.Mesh(armGeo, bodyMat);
            arm.position.x=Math.cos(i*Math.PI/2)*7;
            arm.position.z=Math.sin(i*Math.PI/2)*7;
            arm.rotation.y=i*Math.PI/2;
            arm.castShadow=true;
            group.add(arm);

            // 桨叶
            const rotorGeo = new THREE.BoxGeometry(16,0.3,2);
            const rotor = new THREE.Mesh(rotorGeo, new THREE.MeshStandardMaterial({color:0xcccccc,roughness:0.2,metalness:0.5}));
            rotor.position.copy(arm.position); rotor.position.y+=1.5;
            rotor.userData={baseY:rotor.position.y, speed:0.5+Math.random()*0.5};
            group.add(rotor);
            if (!group.userData.rotors) group.userData.rotors=[];
            group.userData.rotors.push(rotor);
        }
        if (!group.userData.rotors) group.userData.rotors=[];

        // 领导者光环
        if (uav.is_leader) {
            const ringGeo = new THREE.TorusGeometry(10,0.5,16,32);
            const ring = new THREE.Mesh(ringGeo, new THREE.MeshStandardMaterial({color:0xa855f7,emissive:0xa855f7,emissiveIntensity:0.5,roughness:0.2}));
            ring.rotation.x=Math.PI/2; ring.position.y=0.5;
            group.add(ring);
        }

        group.userData.uavId = uav.id;
        group.userData.role = uav.role;
        return group;
    }

    // ===== P2: 尾焰粒子 =====
    _createExhaustParticles(uavMesh) {
        const count=30;
        const geo = new THREE.BufferGeometry();
        const positions = new Float32Array(count*3);
        for (let i=0; i<count*3; i+=3) {
            positions[i]=(Math.random()-0.5)*3;
            positions[i+1]=-3-Math.random()*8;
            positions[i+2]=(Math.random()-0.5)*3;
        }
        geo.setAttribute('position', new THREE.BufferAttribute(positions,3));
        const mat = new THREE.PointsMaterial({color:0xff6600, size:1.5, blending:THREE.AdditiveBlending, depthWrite:false, transparent:true, opacity:0.7});
        const pts = new THREE.Points(geo, mat);
        pts.visible=false;
        uavMesh.add(pts);
        return pts;
    }

    // ===== P2: 雷达探测锥 =====
    _createRadarCone(mesh, range, color) {
        const geo = new THREE.ConeGeometry(range*0.15, range, 32, 1, true);
        geo.translate(0, -range/2, 0);
        const mat = new THREE.MeshBasicMaterial({color, transparent:true, opacity:0.08, depthWrite:false, side:THREE.DoubleSide});
        const cone = new THREE.Mesh(geo, mat);
        cone.rotation.x=Math.PI;
        cone.position.y=-5;
        mesh.add(cone);
        return cone;
    }

    // ===== P2: 爆炸粒子 =====
    _createExplosion(position) {
        const count=80;
        const geo = new THREE.BufferGeometry();
        const posArr = new Float32Array(count*3);
        const velArr = [];
        for (let i=0; i<count; i++) {
            posArr[i*3]=position.x; posArr[i*3+1]=position.y; posArr[i*3+2]=position.z;
            const theta=Math.random()*Math.PI*2, phi=Math.random()*Math.PI;
            const spd=50+Math.random()*150;
            velArr.push({vx:Math.sin(phi)*Math.cos(theta)*spd, vy:Math.sin(phi)*Math.sin(theta)*spd, vz:Math.cos(phi)*spd, life:30+Math.random()*30});
        }
        geo.setAttribute('position', new THREE.BufferAttribute(posArr,3));
        const mat = new THREE.PointsMaterial({color:0xff4400, size:8, blending:THREE.AdditiveBlending, depthWrite:false, transparent:true, opacity:1});
        const pts = new THREE.Points(geo, mat);
        pts.userData={vel:velArr, age:0, maxAge:40};
        this.scene.add(pts);
        return pts;
    }

    // ===== P3: 夜视模式 =====
    setNightMode(on) {
        this.nightMode=on;
        if (on) {
            this.scene.background=new THREE.Color(0x0a0a1e);
            this.scene.fog=new THREE.Fog(0x0a0a1e, 2000, 8000);
            this.sun.intensity=0.15; this.sun.color.set(0x8899cc);
            this.hemi.intensity=0.1;
            this.sky.material.uniforms.topColor.value.set(0x111133);
            this.sky.material.uniforms.bottomColor.value.set(0x222244);
            if (this.terrain) this.terrain.material.emissive=new THREE.Color(0x001100);
        } else {
            this.scene.background=new THREE.Color(0x1a2b4a);
            this.scene.fog=new THREE.Fog(0x1a2b4a, 4000, 15000);
            this.sun.intensity=1.5; this.sun.color.set(0xfff5e6);
            this.hemi.intensity=0.4;
            this.sky.material.uniforms.topColor.value.set(0x3377cc);
            this.sky.material.uniforms.bottomColor.value.set(0x8899bb);
            if (this.terrain) this.terrain.material.emissive=new THREE.Color(0x000000);
        }
    }

    // ===== P3: IR热成像模式 =====
    setIRMode(on) {
        this.irMode=on;
        if (on) {
            this.scene.background=new THREE.Color(0x000022);
            this.sun.intensity=0.1; this.sun.color.set(0x444444);
            this.hemi.intensity=0.05;
            // 目标变红
            Object.values(this.targetMeshes).forEach(m=>{
                if (m.material&&m.material.emissive) m.material.emissive.set(0xff3300);
            });
        } else {
            Object.values(this.targetMeshes).forEach(m=>{
                if (m.material&&m.material.emissive) m.material.emissive.set(0x000000);
            });
        }
    }

    // ===== P3: 军标标签(Sprite) =====
    _createLabel(text, color) {
        const canvas=document.createElement('canvas'); canvas.width=256; canvas.height=64;
        const ctx=canvas.getContext('2d');
        ctx.fillStyle=color||'#ffffff'; ctx.font='bold 24px monospace'; ctx.textAlign='center';
        ctx.fillText(text,128,32);
        const tex=new THREE.CanvasTexture(canvas); tex.minFilter=THREE.LinearFilter;
        const mat=new THREE.SpriteMaterial({map:tex, transparent:true, depthTest:false, depthWrite:false});
        const sprite=new THREE.Sprite(mat); sprite.scale.set(400,100,1);
        return sprite;
    }

    // ===== P2: 通信链路(贝塞尔管道) =====
    _createLinkCurve(p1, p2, color=0x00d4ff) {
        const midY=Math.max(p1.y,p2.y)+200;
        const curve=new THREE.QuadraticBezierCurve3(
            new THREE.Vector3(p1.x,p1.y+10,p1.z),
            new THREE.Vector3((p1.x+p2.x)/2,midY,(p1.z+p2.z)/2),
            new THREE.Vector3(p2.x,p2.y+10,p2.z));
        const geo=new THREE.TubeGeometry(curve,20,2,8,false);
        const mat=new THREE.MeshBasicMaterial({color,transparent:true,opacity:0.3,depthWrite:false});
        return new THREE.Mesh(geo,mat);
    }

    // ===== P0: 相机控制 =====
    setView(mode, uavId) {
        this.viewMode=mode; this.fpvId=null; this.followId=null;
        if (mode==='follow'&&uavId) this.followId=uavId;
        if (mode==='fpv'&&uavId) this.fpvId=uavId;
        if (mode==='topdown') { this.camera.position.set(5000,9000,5000); this.controls.target.set(5000,5000,0); }
    }

    // ===== 数据更新 =====
    update(state) {
        // 记录上一帧存活状态用于爆炸检测
        if (this.lastState) {
            this._prevAlive={};
            Object.values(this.lastState.uavs||{}).forEach(u=>{this._prevAlive[u.id]=u.alive;});
        }
        this.lastState=state;
        if (!state) return;

        // HUD更新
        const setHud=(id,val)=>{const el=document.getElementById(id);if(el)el.textContent=val;};
        setHud('hud-view',this.fpvId?'FPV:'+this.fpvId:this.followId?'跟随:'+this.followId:this.viewMode==='topdown'?'俯视':'自由');
        setHud('hud-night',this.nightMode?'开':'关');
        setHud('hud-ir',this.irMode?'开':'关');
        setHud('hud-radar',this.showRadar?'开':'关');

        const to3D = (pos) => ({x:pos.x, y:pos.z||200, z:pos.y});

        // === UAVs ===
        const seenUavs=new Set();
        Object.values(state.uavs||{}).forEach(uav=>{
            seenUavs.add(uav.id);
            const p3=to3D(uav.position);
            let mesh=this.uavMeshes[uav.id];
            if (!mesh) {
                mesh=this._createUAVMesh(uav);
                this.scene.add(mesh);
                // 尾焰
                mesh.userData.exhaust=this._createExhaustParticles(mesh);
                // 雷达锥
                mesh.userData.radar=this._createRadarCone(mesh, uav.detection_range||3000, 0x00ff88);
                // 标签
                mesh.userData.label=this._createLabel(uav.id, '#'+ROLE_COLORS[uav.role]?.replace('#','')||'ffffff');
                mesh.add(mesh.userData.label); mesh.userData.label.position.set(0,25,0);
                this.uavMeshes[uav.id]=mesh;
            }

            // 位置+朝向
            mesh.position.set(p3.x, p3.y, p3.z);
            const yaw=(uav.attitude?.yaw||0)*Math.PI/180;
            mesh.rotation.set(0, -yaw+Math.PI/2, 0);

            // 桨叶旋转
            const speed=Math.sqrt((uav.velocity?.x||0)**2+(uav.velocity?.y||0)**2);
            if (mesh.userData.rotors) mesh.userData.rotors.forEach(r=>{
                r.rotation.y+=0.3+speed*0.02;
                r.position.y=r.userData.baseY+Math.sin(Date.now()*0.01)*0.5;
            });

            // 尾焰
            if (mesh.userData.exhaust) mesh.userData.exhaust.visible=speed>30;

            // 存活/摧毁
            mesh.visible=uav.alive;
            if (!uav.alive&&mesh.userData.exhaust) mesh.userData.exhaust.visible=false;

            // 规避状态: 橙色脉冲
            if (uav.state==='EVADING') {
                const pulse=(Math.sin(Date.now()*0.01)+1)/2;
                mesh.children.forEach(c=>{if(c.material&&c.material.emissive) c.material.emissive.set(0xff6600*pulse);});
            }
        });

        // 清理不存在的UAV
        Object.keys(this.uavMeshes).forEach(id=>{if(!seenUavs.has(id)){this.scene.remove(this.uavMeshes[id]);delete this.uavMeshes[id];}});

        // === 拦截器 (红色威胁) ===
        const intrState=state.interceptors;
        if (intrState&&intrState.units) {
            const seenIntr=new Set();
            Object.values(intrState.units).forEach(intr=>{
                seenIntr.add(intr.id);
                const p3=to3D(intr.position);
                let mesh=this.intrMeshes[intr.id];
                if (!mesh) {
                    mesh=this._createUAVMesh({id:intr.id,role:'striker'});
                    mesh.children.forEach(c=>{if(c.material&&c.material.color) c.material.color.set(0xff2222);});
                    mesh.userData.radar=this._createRadarCone(mesh, intr.detection_range||3500, 0xff2222);
                    mesh.userData.label=this._createLabel(intr.id,'#ff4444'); mesh.add(mesh.userData.label);
                    mesh.userData.label.position.set(0,25,0);
                    this.scene.add(mesh); this.intrMeshes[intr.id]=mesh;
                }
                mesh.position.set(p3.x,p3.y,p3.z);
                mesh.visible=intr.alive;

                // P2: 追踪光束
                if (intr.current_target&&intr.state==='ENGAGING') {
                    if (mesh.userData.radar) mesh.userData.radar.material.opacity=0.3;
                    const targetUav=state.uavs?.[intr.current_target];
                    if (targetUav) {
                        const tp=to3D(targetUav.position);
                        const laserGeo=new THREE.CylinderGeometry(0.5,0.5,Math.sqrt((tp.x-p3.x)**2+(tp.y-p3.y)**2+(tp.z-p3.z)**2),8);
                        if (!mesh.userData.laser) {
                            laserGeo.translate(0,-5,0);
                            const laserMat=new THREE.MeshBasicMaterial({color:0xff0000,transparent:true,opacity:0.5,depthWrite:false});
                            mesh.userData.laser=new THREE.Mesh(laserGeo,laserMat);
                            mesh.add(mesh.userData.laser);
                        }
                    }
                }
            });
            Object.keys(this.intrMeshes).forEach(id=>{if(!seenIntr.has(id)){this.scene.remove(this.intrMeshes[id]);delete this.intrMeshes[id];}});
        }

        // === 目标 ===
        Object.values(state.targets||{}).forEach(t=>{
            const p3=to3D(t.position);
            let mesh=this.targetMeshes[t.id];
            if (!mesh) {
                const types={command_post:{w:60,h:30,d:40,color:0xcc8844},radar_station:{w:30,h:40,d:30,color:0x44aacc},vehicle_convoy:{w:20,h:15,d:35,color:0x88aa44}};
                const cfg=types[t.type]||types.command_post;
                const geo=new THREE.BoxGeometry(cfg.w,cfg.h,cfg.d);
                const mat=new THREE.MeshStandardMaterial({color:cfg.color,roughness:0.5,metalness:0.4});
                mesh=new THREE.Mesh(geo,mat); mesh.castShadow=true; mesh.receiveShadow=true;
                mesh.userData.label=this._createLabel(t.id,'#ff8c00'); mesh.add(mesh.userData.label);
                mesh.userData.label.position.set(0,cfg.h+15,0);
                // 防御穹顶
                const domeGeo=new THREE.SphereGeometry(t.defense_range||2000,32,16,0,Math.PI*2,0,Math.PI/2);
                const domeMat=new THREE.MeshBasicMaterial({color:0xff8800,transparent:true,opacity:0.06,depthWrite:false,side:THREE.DoubleSide});
                mesh.userData.dome=new THREE.Mesh(domeGeo,domeMat);
                mesh.add(mesh.userData.dome);
                this.scene.add(mesh); this.targetMeshes[t.id]=mesh;
            }
            mesh.position.set(p3.x,15,p3.z);
            mesh.visible=!t.destroyed;
            if (t.destroyed) {
                mesh.material.color.set(0x333333);
                if (mesh.userData.dome) mesh.userData.dome.visible=false;
            }
        });

        // === P2: 通信链路 ===
        if (this.showLinks) {
            const topo=state.network?.topology||{};
            const drawn=new Set();
            Object.entries(topo).forEach(([uid,neighbors])=>{
                const u1=state.uavs?.[uid]; if(!u1||!u1.alive) return;
                neighbors.forEach(nid=>{
                    const key=[uid,nid].sort().join('-');
                    if (drawn.has(key)) return; drawn.add(key);
                    const u2=state.uavs?.[nid]; if(!u2||!u2.alive) return;
                    const lid=`link_${key}`;
                    if (this.linkLines[lid]) this.scene.remove(this.linkLines[lid]);
                    const line=this._createLinkCurve(to3D(u1.position),to3D(u2.position));
                    this.scene.add(line); this.linkLines[lid]=line;
                });
            });
        }

        // === P3: 相机跟随 ===
        if (this.followId) {
            const mesh=this.uavMeshes[this.followId];
            if (mesh) {
                const pos=mesh.position;
                this.camera.position.lerp(new THREE.Vector3(pos.x-400,pos.y+300,pos.z+500),0.05);
                this.controls.target.lerp(new THREE.Vector3(pos.x,pos.y,pos.z),0.1);
            }
        }
        if (this.fpvId) {
            const mesh=this.uavMeshes[this.fpvId];
            if (mesh) {
                const pos=mesh.position;
                const yaw=mesh.rotation.y;
                this.camera.position.copy(new THREE.Vector3(pos.x,pos.y+15,pos.z).add(new THREE.Vector3(Math.sin(yaw)*50,0,Math.cos(yaw)*50)));
                this.controls.target.copy(new THREE.Vector3(pos.x+Math.sin(yaw)*500,pos.y,pos.z+Math.cos(yaw)*500));
            }
        }
    }

    // ===== 事件 =====
    _bindEvents() {
        eventBus.on('state-update',(s)=>this.update(s));

        // 检测UAV摧毁 → 触发爆炸
        if (this._prevAlive) {
            Object.entries(this._prevAlive).forEach(([id,wasAlive])=>{
                const now=this.lastState?.uavs?.[id];
                if (wasAlive && now && !now.alive) this.triggerExplosion(id);
            });
        }

        window.addEventListener('resize',()=>{
            if(!this.container)return;
            this.W=this.container.clientWidth; this.H=this.container.clientHeight;
            this.camera.aspect=this.W/this.H; this.camera.updateProjectionMatrix();
            this.renderer.setSize(this.W,this.H);
        });

        // 键盘快捷键
        window.addEventListener('keydown',(e)=>{
            if (e.key==='n') { e.preventDefault(); this.setNightMode(!this.nightMode); }
            if (e.key==='i') { e.preventDefault(); this.setIRMode(!this.irMode); }
            if (e.key==='r') { e.preventDefault(); this.showRadar=!this.showRadar; }
            if (e.key==='1') this.setView('topdown');
            if (e.key==='2') { const uavs=Object.values(this.lastState?.uavs||{}).filter(u=>u.alive); if(uavs.length)this.setView('follow',uavs[0].id); }
            if (e.key==='3') { const uavs=Object.values(this.lastState?.uavs||{}).filter(u=>u.alive); if(uavs.length)this.setView('fpv',uavs[0].id); }
        });
    }

    // ===== 渲染循环 =====
    _animLoop() {
        requestAnimationFrame(()=>this._animLoop());
        if (!this.container||!this.container.offsetParent) return; // 标签页不可见时跳过
        this.controls.update();
        this.renderer.render(this.scene, this.camera);

        // 清理旧粒子
        this.scene.children.filter(c=>c.isPoints&&c.userData.age!==undefined).forEach(p=>{
            p.userData.age++;
            if (p.userData.age>p.userData.maxAge) { this.scene.remove(p); return; }
            const pos=p.geometry.attributes.position;
            const vel=p.userData.vel;
            for (let i=0;i<pos.count;i++) {
                pos.array[i*3]+=vel[i].vx*0.05; pos.array[i*3+1]+=vel[i].vy*0.05; pos.array[i*3+2]+=vel[i].vz*0.05;
                vel[i].vy-=2;
            }
            pos.needsUpdate=true;
            p.material.opacity=1-p.userData.age/p.userData.maxAge;
        });

        // 水波动画
        if (this.water) this.water.position.z+=Math.sin(Date.now()*0.001)*0.5;
    }

    // 获取被摧毁UAV — 触发爆炸
    triggerExplosion(uavId) {
        const mesh=this.uavMeshes[uavId];
        if (mesh) this._createExplosion(mesh.position);
    }
}

// 全局
let swarmScene3D=null;
eventBus.on('init',()=>{
    swarmScene3D=new SwarmScene3D('scene3d-container');
    window._scene3d=swarmScene3D;
});
