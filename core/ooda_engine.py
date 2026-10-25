"""
OODA 决策循环引擎 v2.0
======================
P0: 显式OODA时间标注 + 循环度量 + 阶段计时
P1: 威胁矩阵 + 机会窗口分析 + 意图推断 + 态势预测
P2: 多方案决策对比 + 闭环反馈学习
P3: 历史态势相似性匹配 + 经验复用

John Boyd: "Orientation is the Schwerpunkt"
"""
import math, time, random
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
from collections import defaultdict, deque
import hashlib, json

MAP_W, MAP_H = 10000, 10000

class OODAPhase(Enum):
    OBSERVE="observe"; ORIENT="orient"; DECIDE="decide"; ACT="act"; FEEDBACK="feedback"

@dataclass
class ThreatVector:
    source_id:str; distance:float; speed:float; direction_deg:float
    closing_rate:float; threat_level:str; estimated_intercept_tick:int

@dataclass
class OpportunityWindow:
    target_id:str; window_open_tick:int; window_close_tick:int
    window_duration:int; success_probability:float; risk_level:str

@dataclass
class DecisionOption:
    option_id:str; action:str; expected_outcome:str; success_prob:float
    risk:str; cost:float; score:float=0.0; pros:list=field(default_factory=list); cons:list=field(default_factory=list)

@dataclass
class OODACycle:
    cycle_id:int; agent_id:str; agent_role:str; tick_start:int
    observe_ticks:int=0; orient_ticks:int=0; decide_ticks:int=0; act_ticks:int=0
    observe_quality:float=0; orient_quality:float=0; decide_quality:float=0; act_quality:float=0
    threat_vectors:list=field(default_factory=list)
    opportunity_windows:list=field(default_factory=list)
    decision_options:list=field(default_factory=list)
    selected_option:Optional[str]=None
    # P2: 闭环反馈
    action_expected:dict=field(default_factory=dict)
    action_actual:dict=field(default_factory=dict)
    effectiveness:float=0.0
    total_ticks:int=0; success:bool=False; feedback:str=""; lessons:list=field(default_factory=list)

    @property
    def total_time_ms(self)->float: return self.total_ticks*100
    @property
    def ooda_score(self)->float:
        speed_sc = max(0, 1.0 - self.total_ticks/50)
        qual_sc = 0.2*self.observe_quality + 0.35*self.orient_quality + 0.25*self.decide_quality + 0.2*self.act_quality
        return round(speed_sc*0.4 + qual_sc*0.6, 3)
    @property
    def orient_depth(self)->int:
        return sum(1 for x in [self.threat_vectors, self.opportunity_windows, self.decision_options] if x)

class OODAEngine:
    """P0-P3 完整OODA引擎"""

    def __init__(self):
        self.cycles:dict[str,list[OODACycle]]=defaultdict(list)
        self.current:dict[str,OODACycle]={}
        self.phases:dict[str,OODAPhase]={}
        self._phase_start_ticks:dict[str,dict]={}  # agent_id -> {phase: start_tick}
        self.ooda_advantage=0.0; self._blue_avg=0.0; self._red_avg=0.0
        self.total_completed=0; self.history:list=[]
        # P3: 历史记忆
        self.memory_bank:dict[str,list]=defaultdict(list)  # agent_id -> [situation_signatures]
        self.memory_max=200

    # ===== P0: 显式阶段标注 =====
    def start_cycle(self, aid:str, role:str, tick:int):
        c=OODACycle(cycle_id=len(self.cycles.get(aid,[]))+1, agent_id=aid, agent_role=role, tick_start=tick)
        self.current[aid]=c; self.phases[aid]=OODAPhase.OBSERVE
        self._phase_start_ticks[aid]={OODAPhase.OBSERVE:tick}

    def mark_phase(self, aid:str, phase:OODAPhase, tick:int, quality:float=0.0):
        prev=self.phases.get(aid)
        cycle=self.current.get(aid)
        if not cycle: return

        # 记录上一阶段耗时和结束
        pts=self._phase_start_ticks.get(aid,{})
        if prev and prev in pts:
            elapsed=tick-pts[prev]
            if prev==OODAPhase.OBSERVE: cycle.observe_ticks=max(1,elapsed); cycle.observe_quality=quality
            elif prev==OODAPhase.ORIENT: cycle.orient_ticks=max(1,elapsed); cycle.orient_quality=quality
            elif prev==OODAPhase.DECIDE: cycle.decide_ticks=max(1,elapsed); cycle.decide_quality=quality
            elif prev==OODAPhase.ACT: cycle.act_ticks=max(1,elapsed); cycle.act_quality=quality

        self.phases[aid]=phase; pts[phase]=tick

    def complete_cycle(self, aid:str, tick:int, success:bool=True, feedback:str="", action_result:dict=None)->Optional[OODACycle]:
        cycle=self.current.pop(aid,None)
        if not cycle: return None
        # 完成ACT阶段
        pts=self._phase_start_ticks.get(aid,{})
        if OODAPhase.ACT in pts:
            cycle.act_ticks=max(1,tick-pts[OODAPhase.ACT])

        cycle.total_ticks=tick-cycle.tick_start; cycle.success=success; cycle.feedback=feedback

        # P2: 闭环反馈 - 评估行动效果
        if action_result and cycle.action_expected:
            cycle.action_actual=action_result
            cycle.effectiveness=self._calc_effectiveness(cycle.action_expected, action_result)
            if cycle.effectiveness<0.5:
                cycle.lessons.append(f"Action underperformed (effectiveness={cycle.effectiveness:.0%})")

        self.cycles[aid].append(cycle); self.total_completed+=1
        # P3: 存历史记忆
        sig=self._build_situation_signature(cycle)
        self.memory_bank[aid].append(sig)
        if len(self.memory_bank[aid])>self.memory_max:
            self.memory_bank[aid]=self.memory_bank[aid][-self.memory_max:]

        self.history.append({"cycle_id":cycle.cycle_id,"agent_id":aid,"total_ticks":cycle.total_ticks,
            "ooda_score":cycle.ooda_score,"orient_depth":cycle.orient_depth,"success":success,"effectiveness":cycle.effectiveness})
        if len(self.history)>100: self.history=self.history[-100:]
        return cycle

    def _calc_effectiveness(self, expected:dict, actual:dict)->float:
        """评估行动效果：实际 vs 预期"""
        score=0.0; n=0
        if "damage_dealt" in expected and "damage_dealt" in actual:
            ratio=min(1.5, actual["damage_dealt"]/max(1,expected["damage_dealt"]))
            score+=min(1,ratio); n+=1
        if "distance_closed" in expected and "distance_closed" in actual:
            ratio=min(1.5, actual["distance_closed"]/max(1,expected["distance_closed"]))
            score+=min(1,ratio); n+=1
        if "threat_avoided" in expected:
            score+=1.0 if actual.get("threat_avoided",False) else 0.0; n+=1
        if "uav_preserved" in expected:
            score+=1.0 if actual.get("uav_preserved",True) else 0.0; n+=1
        return score/max(n,1)

    # ===== P1: 威胁矩阵 =====
    def analyze_threat_matrix(self, uav_pos:dict, interceptors:dict)->list[ThreatVector]:
        threats=[]
        for iid, intr in interceptors.items():
            if not intr.get("alive",True): continue
            dx=uav_pos["x"]-intr["position"]["x"]; dy=uav_pos["y"]-intr["position"]["y"]
            dist=math.sqrt(dx*dx+dy*dy)
            speed=math.sqrt(intr["velocity"]["x"]**2+intr["velocity"]["y"]**2)
            direction=math.degrees(math.atan2(-dy,-dx))%360
            speed_diff=max(1,speed-80)
            eta=int(dist/speed_diff) if speed_diff>0 else 999
            if dist<500: lvl="critical"
            elif dist<1000: lvl="high"
            elif dist<3000: lvl="medium"
            else: lvl="low"
            threats.append(ThreatVector(source_id=iid,distance=dist,speed=speed,direction_deg=direction,
                closing_rate=speed,threat_level=lvl,estimated_intercept_tick=eta))
        threats.sort(key=lambda t:t.distance)
        return threats

    # ===== P1: 机会窗口 =====
    def analyze_opportunity_windows(self, uav_pos:dict, targets:dict, interceptors:dict)->list[OpportunityWindow]:
        windows=[]; uav_spd=80
        for tid, t in targets.items():
            if t.get("destroyed") or t.get("health",100)<=0: continue
            dt=math.sqrt((uav_pos["x"]-t["position"]["x"])**2+(uav_pos["y"]-t["position"]["y"])**2)
            eta_target=int(dt/uav_spd)
            min_int_eta=999
            for iid,intr in interceptors.items():
                if not intr.get("alive",True): continue
                di=math.sqrt((uav_pos["x"]-intr["position"]["x"])**2+(uav_pos["y"]-intr["position"]["y"])**2)
                ispd=math.sqrt(intr["velocity"]["x"]**2+intr["velocity"]["y"]**2) or 90
                min_int_eta=min(min_int_eta,int(di/max(1,ispd-uav_spd)))
            wdur=min_int_eta-eta_target
            if wdur>0:
                windows.append(OpportunityWindow(target_id=tid,window_open_tick=eta_target,
                    window_close_tick=min_int_eta,window_duration=wdur,
                    success_probability=min(0.95,wdur/10),
                    risk_level="low" if wdur>10 else "medium" if wdur>3 else "high"))
        windows.sort(key=lambda w:w.success_probability,reverse=True)
        return windows

    # ===== P2: 多方案决策 =====
    def generate_decision_options(self, uav_state:dict, threats:list, windows:list)->list[DecisionOption]:
        opts=[]
        health=uav_state.get("health",100)
        has_task=uav_state.get("current_task") is not None

        # 方案A: 攻击最近目标窗口
        if windows:
            w=windows[0]
            opts.append(DecisionOption(option_id="A_engage",action="ENGAGE",
                expected_outcome=f"Attack {w.target_id} within {w.window_duration}tick window",
                success_prob=w.success_probability,risk=w.risk_level,cost=0.3,
                pros=[f"Window duration: {w.window_duration}tick",f"Success prob: {w.success_probability:.0%}"],
                cons=["Exposes UAV to interceptor fire"]))

        # 方案B: 规避后重新接敌
        if threats:
            t=threats[0]
            prob=max(0.3,1.0-t.distance/3000)
            opts.append(DecisionOption(option_id="B_evade",action="EVADE",
                expected_outcome=f"Evade {t.source_id} then re-engage",
                success_prob=prob,risk="medium",cost=0.5,
                pros=["Higher survival probability","Preserves UAV for later"],
                cons=["Misses immediate strike opportunity"]))

        # 方案C: 呼叫支援
        if threats and len(threats)>=2:
            opts.append(DecisionOption(option_id="C_support",action="COORDINATE",
                expected_outcome="Request wingman support for coordinated strike",
                success_prob=0.75,risk="low",cost=0.4,
                pros=["Shared risk","Higher combined firepower"],
                cons=["Requires nearby friendly UAV","Coordination overhead"]))

        # 方案D: 保持当前行动
        if has_task:
            opts.append(DecisionOption(option_id="D_continue",action="CONTINUE",
                expected_outcome="Continue current task",
                success_prob=0.6,risk="medium",cost=0.1,
                pros=["No disruption","Momentum maintained"],
                cons=["May be suboptimal in changing situation"]))

        # 评分
        for o in opts:
            o.score = o.success_prob*0.5 + (1-o.cost)*0.3
            if o.risk=="low": o.score+=0.1
            elif o.risk=="high": o.score-=0.1
            if health<30 and o.action=="EVADE": o.score+=0.2

        opts.sort(key=lambda o:o.score,reverse=True)
        return opts

    # ===== P3: 历史相似性匹配 =====
    def find_similar_situations(self, agent_id:str, current_signature:dict, top_k:int=3)->list[dict]:
        memory=self.memory_bank.get(agent_id,[])
        if not memory: return []

        scored=[]
        for m in memory:
            sim=self._cosine_similarity(current_signature,m)
            scored.append((sim,m))

        scored.sort(key=lambda x:x[0],reverse=True)
        matches=[]
        for sim,m in scored[:top_k]:
            matches.append({"similarity":round(sim,3),"situation":m})
        return matches

    def _build_situation_signature(self, cycle:OODACycle)->dict:
        return {"total_ticks":cycle.total_ticks,"orient_depth":cycle.orient_depth,
            "success":cycle.success,"threat_count":len(cycle.threat_vectors),
            "window_count":len(cycle.opportunity_windows),"effectiveness":cycle.effectiveness}

    def _cosine_similarity(self, a:dict, b:dict)->float:
        keys=["total_ticks","orient_depth","threat_count","window_count","effectiveness"]
        va=[a.get(k,0) for k in keys]; vb=[b.get(k,0) for k in keys]
        dot=sum(va[i]*vb[i] for i in range(len(va)))
        na=math.sqrt(sum(x*x for x in va)) or 1
        nb=math.sqrt(sum(x*x for x in vb)) or 1
        return dot/(na*nb)

    # ===== OODA 优势 =====
    def compute_ooda_advantage(self, blue_ids:list[str], red_ids:list[str])->float:
        bt=[c.total_ticks for aid in blue_ids for c in self.cycles.get(aid,[])[-5:]]
        rt=[c.total_ticks for aid in red_ids for c in self.cycles.get(aid,[])[-5:]]
        self._blue_avg=sum(bt)/len(bt) if bt else 0
        self._red_avg=sum(rt)/len(rt) if rt else 0
        self.ooda_advantage=(self._red_avg-self._blue_avg)/self._red_avg if self._red_avg>0 else 0.0
        return self.ooda_advantage

    def get_advantage_rating(self)->str:
        a=self.ooda_advantage
        if a>0.5: return "S: Dominant"
        elif a>0.25: return "A: Superior"
        elif a>0.1: return "B: Advantage"
        elif a>-0.1: return "C: Parity"
        elif a>-0.25: return "D: Disadvantage"
        return "E: Dominated"

    def get_ooda_state(self)->dict:
        agent_stats={}
        for aid,cycles in self.cycles.items():
            if cycles:
                recent=cycles[-10:]
                agent_stats[aid]={
                    "avg_ticks":round(sum(c.total_ticks for c in recent)/len(recent),1),
                    "avg_score":round(sum(c.ooda_score for c in recent)/len(recent),3),
                    "avg_orient_depth":round(sum(c.orient_depth for c in recent)/len(recent),1),
                    "avg_effectiveness":round(sum(c.effectiveness for c in recent)/len(recent),3),
                    "total_cycles":len(cycles),
                    "last_phase":self.phases.get(aid,OODAPhase.OBSERVE).value,
                    "in_cycle":aid in self.current,
                    "lessons_recent":[c.lessons for c in recent if c.lessons],
                }

        # P0: 各阶段平均耗时分布
        phase_breakdown={}
        for aid,cycles in self.cycles.items():
            if cycles:
                recent=cycles[-10:]
                phase_breakdown[aid]={
                    "observe_avg":round(sum(c.observe_ticks for c in recent)/len(recent),1),
                    "orient_avg":round(sum(c.orient_ticks for c in recent)/len(recent),1),
                    "decide_avg":round(sum(c.decide_ticks for c in recent)/len(recent),1),
                    "act_avg":round(sum(c.act_ticks for c in recent)/len(recent),1),
                }

        return {
            "ooda_advantage":round(self.ooda_advantage,3),
            "advantage_rating":self.get_advantage_rating(),
            "blue_avg_time_ms":round(self._blue_avg*100,1),
            "red_avg_time_ms":round(self._red_avg*100,1),
            "total_cycles_completed":self.total_completed,
            "agents":agent_stats,
            "phase_breakdown":phase_breakdown,
            "recent_history":self.history[-30:],
            "inside_enemy_loop":self.ooda_advantage>0.1,
            # P1: 最近一次威胁分析摘要
            "latest_threats":{},
            "latest_windows":{},
            # P3: 历史匹配状态
            "memory_bank_size":sum(len(v) for v in self.memory_bank.values()),
        }

    def reset(self):
        self.cycles.clear(); self.current.clear(); self.phases.clear()
        self.ooda_advantage=0.0; self._blue_avg=0.0; self._red_avg=0.0
        self.total_completed=0; self.history.clear(); self.memory_bank.clear()
