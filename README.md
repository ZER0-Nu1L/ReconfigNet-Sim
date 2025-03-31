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
|GET|`/ocs_mapping`|Retrieve current mapping configuration|None|`{"pi": [2,1,4,3], "status": "ready"}`|
|POST|`/ocs_mapping`|Update mapping configuration|`{"new_pi": [3,4,1,2]}`|`{"status": "success", "new_pi": [...]}`|

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


(tty1) Using http requests to call northbound interface to check/change the configuration of OCS.
- GET request (num_host=8): 
    ```Bash
    docker exec -it <container_id> python -c '
    import socket; import json;
    data = {"new_pi": [3,4,1,2,7,8,5,6]};
    body = json.dumps(data).encode("utf-8");
    headers = (
        "POST /ocs_mapping HTTP/1.1\r\n"
        "Host: localhost\r\n"
        "Content-Length: {}\r\n".format(len(body)) +
        "Content-Type: application/json\r\n"
        "\r\n"
    );
    s = socket.socket();
    s.connect(("localhost",5000));
    s.sendall(headers.encode("utf-8") + body);

    # Read the response from the server
    response = b""
    while True:
        chunk = s.recv(4096)
        if not chunk:
            break
        response += chunk
    print(response.decode())
    s.close()
    '
    ```
- POST request (num_host=8): 
    ```Bash
    docker exec -it <container_id> python -c '
    import socket; import json;
    data = {"new_pi": [3,4,1,2,7,8,5,6]};
    body = json.dumps(data).encode("utf-8");
    headers = (
        "POST /ocs_mapping HTTP/1.1\r\n"
        "Host: localhost\r\n"
        "Content-Length: {}\r\n".format(len(body)) +
        "Content-Type: application/json\r\n"
        "\r\n"
    );
    s = socket.socket();
    s.connect(("localhost",5000));
    s.sendall(headers.encode("utf-8") + body);

    # Read the response from the server
    response = b""
    while True:
        chunk = s.recv(4096)
        if not chunk:
            break
        response += chunk
    print(response.decode())
    s.close()
    '
    ```

- Check `<container_id>` using `docker ps | grep p4app` on your host machine.
- Though using Python socket package to send http requests is a little bit troublesome things, tools (like curl, wget) is not support in p4app container.


To support more future (like modify the number of host and http port, or enabling debug mode), check configuration file `config/p4app.json`.


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
$SDE/run_bfshell.sh -b <path-to-project>/ReconfigNet-Sim/ocs.tofino/net-ctrl/setup.py -i
```
> Use -i to stay in the interactive mode after the script has been executed.

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

To support more future (like modify the number of host and http port), check configuration file `net-ctrl/config/project_conf.json`.