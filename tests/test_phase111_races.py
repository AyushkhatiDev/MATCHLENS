"""Controlled local async boundaries; no cloud or real-time sleeping."""
import asyncio
import threading
from unittest.mock import patch
import httpx
from simulator.presentation.api import create_app,DEFAULT_PATH
from simulator.production.config import AppConfig


def test_simultaneous_start_does_not_create_two_replays():
 async def run():
  service=create_app(DEFAULT_PATH,AppConfig()).state.service
  entered=asyncio.Event();release=asyncio.Event();calls=[];replays=[]
  async def close():calls.append(1);entered.set();await release.wait()
  async def idle():replays.append(1);await asyncio.Event().wait()
  service.close_workflows=close;service.replay=idle
  first=asyncio.create_task(service.control('start',100));await entered.wait()
  second=asyncio.create_task(service.control('start',100));await asyncio.sleep(0)
  release.set();await asyncio.gather(first,second);await asyncio.sleep(0)
  assert len(calls)==1 and len(replays)==1
  service.task.cancel();await asyncio.gather(service.task,return_exceptions=True)
 asyncio.run(run())


def test_late_guide_cannot_override_newer_guide_or_reset():
 async def run(reset):
  entered=threading.Event();release=threading.Event();calls=0
  def guide(_path):
   nonlocal calls
   calls+=1
   if calls==1:entered.set();assert release.wait(5)
   return [{'stage':1,'timestamp':600},{'stage':2,'timestamp':1200}]
  with patch('simulator.presentation.temporal.guide',guide):app=create_app(DEFAULT_PATH,AppConfig())
  service=app.state.service
  async def idle():await asyncio.Event().wait()
  service.replay=idle;service.status='running';service.task=asyncio.create_task(idle())
  async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
   path='/api/matches/'+service.match.match_id
   with patch('simulator.presentation.temporal.guide',guide):
    first=asyncio.create_task(client.post(path+'/guide/1'))
    assert await asyncio.to_thread(entered.wait,5)
    if reset:
     service.task.cancel();await asyncio.gather(service.task,return_exceptions=True)
     service.status='complete'
     assert (await client.post(path+'/demo',json={'action':'start','speed':100})).status_code==200
    else:assert (await client.post(path+'/guide/2')).status_code==200
    release.set();response=await first
    assert response.status_code==409
    assert service.pause_at==(None if reset else 1200)
  await service.close()
 for reset in (False,True):asyncio.run(run(reset))

def test_close_while_start_is_waiting_cannot_create_zombie_replay():
 async def run():
  service=create_app(DEFAULT_PATH,AppConfig()).state.service
  entered=asyncio.Event();release=asyncio.Event();count=0;started=[]
  async def close_workflows():
   nonlocal count
   count+=1
   if count==1:entered.set();await release.wait()
  async def idle():started.append(True);await asyncio.Event().wait()
  service.close_workflows=close_workflows;service.replay=idle
  pending=asyncio.create_task(service.control('start',100));await entered.wait()
  await service.close();release.set();await asyncio.gather(pending,return_exceptions=True);await asyncio.sleep(0)
  if service.task:service.task.cancel();await asyncio.gather(service.task,return_exceptions=True)
  assert not started
 asyncio.run(run())
