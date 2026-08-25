# ReconfigNet-Sim

## Introduction

This repo is using programmable network to simulate reconfigable network (demand-aware).

There are two implementation, one (`ocs.p4app`) using p4app (using Mininet backend), the other (`ocs.tofino`) using Intel® P4 Studio (using tofino backend).


```Bash
git clone https://github.com/ZER0-Nu1L/ReconfigNet-Sim
cd ReconfigNet-Sim
git submodule update --init
```

We use P4 Switch to simulate optical circuit switch (OCS), and a Control Plane provides northbound interface to handle network reconfigurable requests.

|Method|Path|Functionality|Request Body Example|Response Example|
|-|-|-|-|-|
|GET|`/ocs_mapping`|Retrieve current mapping configuration|None|`{"pi": [2,1,4,3], "mode": "ocs", "status": "ready"}`|
|POST|`/ocs_mapping`|Update mapping configuration|`{"new_pi": [3,4,1,2], "delay_us": 250}`|`{"status": "success", "result": "updated", ...}`|
|GET|`/ocs_mode`|Retrieve the current OCS/debug mode|None|`{"mode": "ocs", "active_entries": 4, ...}`|
|POST|`/ocs_mode`|Switch between paired OCS and full-mesh debug mode|`{"mode": "debug"}`|`{"status": "success", "result": "updated", ...}`|

- An OCS core implements strict bijective connectivity with an $N{\times}N$ MEMS mirror array that physically connects ingress-egress port pairs via optical beam steering. 
- This intrinsic **bijective mapping** can be formalized as a bijective pairings $\mathbf{P} \in \{0,1\}^{N \times N}$ where $p_{ij}=1$ $\iff$ input $i$ connects to output $j$.
- We use the list `pi`/`new_pi` to simplify the representation and transmission of this mapping relationship. 
- `for ingress_port, egress_port in enumerate(pi, start=1)` will convert this list into a complete map.

## p4app version usage

- p4app approach have two sub-implementation, one using p4app main branch, and the other using rc-2.0.0 branch
    rc-2.0.0 branch version (in `ocs.p4app/ocs.p4app-rc2`) is more complete (*Recommend*).
- p4app run in docker. So it can even run on your laptop (macOS/Windows/Linux) with docker.


(tty0) Using p4app to build a simulated reconfigurable network constructed via Mininet-P4Switch.
```Bash
cd ./ocs.p4app/ocs.p4app-rc2
sudo make run

# ...

mininet> py net.pingAll(timeout = 1)
```


(tty1) Use HTTP requests to query or change the simulated OCS. The p4app
container does not include curl, so the examples use Python's standard HTTP
client:

```Bash
docker exec -it <container_id> python -c '
import http.client, json

def request(method, path, payload=None):
    body = json.dumps(payload) if payload is not None else None
    headers = {"Content-Type": "application/json"} if body else {}
    conn = http.client.HTTPConnection("localhost", 5000)
    conn.request(method, path, body=body, headers=headers)
    response = conn.getresponse()
    print(response.status, response.read().decode("utf-8"))
    conn.close()

request("GET", "/ocs_mapping")
request("POST", "/ocs_mode", {"mode": "debug"})
request("POST", "/ocs_mode", {"mode": "ocs", "delay_us": 250})
request("POST", "/ocs_mapping", {
    "new_pi": [4,3,2,1,8,7,6,5],
    "delay_ms": 10
})
'
```

Mapping and mode changes use break-before-make table programming. `delay_us`
(0--1,000,000) and `delay_ms` (0--1000) are optional and mutually exclusive.
The API serializes concurrent updates, treats unchanged requests as idempotent,
and attempts to restore the previous table contents after a programming error.

- Check `<container_id>` using `docker ps | grep p4app` on your host machine.
- The p4app container relies on Python's standard library for these requests
  because tools such as curl and wget are not installed.


Use `config/p4app.json` to select L2/L3 forwarding, an even host count from 2
through 8, the initial symmetric mapping, debugger/API enablement, and the REST
listen address. Runtime OCS/debug mode is controlled through `/ocs_mode` and is
separate from the L2/L3 forwarding setting.


## Tofino version

> Tofino version now assumes that you have configured the basic environment. 
We will add more details for development environment in near future.

```Bash
cd ./ocs.tofino
```

Split your terminal to several panes and execute `. ~/tools/set_sde.bash` for all of them.

(tty0) Compile the P4 program
```Bash
~/tools/p4_build.sh ./p4src/ocs.p4
```


(tty1) Run the Tofino Model
```Bash
sudo ~/tools/veth_setup.sh
$SDE/run_tofino_model.sh -p ocs --log-dir ./logs

# ...
# In the end 
sudo ~/tools/veth_teardown.sh
```

(tty2) Launch the driver
```Bash
cd ./net-ctrl
$SDE/run_switchd.sh -p ocs
```

(tty3) Switch entries install and lauch the Control plane
```Bash
cd ./net-ctrl
export OCS_CONFIG_FILE=/absolute/path/to/deployment-profile.json
$SDE/run_bfshell.sh -b <path-to-project>/ReconfigNet-Sim/ocs.tofino/net-ctrl/setup.py -i
```
> Use -i to stay in the interactive mode after the script has been executed.
> `OCS_CONFIG_FILE` is mandatory. Copy `net-ctrl/config/project_conf.json`
> as a schema example and keep real management addresses, endpoint MACs and
> front-panel/dev-port assignments in a deployment repository.

(tt4)
```Bash
sudo python ./net-ctrl/net-util/pingall.py

# GET request (num_host=8):
curl http://localhost:5000/ocs_mapping

# POST request (num_host=8):
curl -X POST -H "Content-Type: application/json" \
-d '{"new_pi":[3,4,1,2,8,7,5,6]}' \
http://localhost:5000/ocs_mapping
```

The example profile at `net-ctrl/config/project_conf.json` uses documentation
addresses and is not a hardware deployment profile. See
[`docs/ocs-control-semantics.md`](docs/ocs-control-semantics.md) for the OCS,
debug and break-before-make semantics shared by the BMv2 and Tofino backends.
