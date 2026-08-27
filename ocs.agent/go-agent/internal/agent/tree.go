package agent

func (a *Agent) OpenConfigTree() map[string]any {
	snapshot := a.Snapshot()
	peers := make(map[string]string, len(snapshot.Connections)*2)
	for _, connection := range snapshot.Connections {
		peers[connection.NearPortName] = connection.FarPortName
		peers[connection.FarPortName] = connection.NearPortName
	}

	components := make([]any, 0, a.inventory.Len())
	for _, port := range a.inventory.Ports() {
		peer, connected := peers[port.Name]
		portStatus := "OFF"
		if snapshot.Status == "error" {
			portStatus = "FAILED"
			connected = false
		} else if snapshot.Mode != "ocs" {
			portStatus = "BLOCKED"
			connected = false
		} else if connected {
			portStatus = "TUNED"
		}
		connectionState := map[string]any{"connected": connected}
		if peer != "" {
			connectionState["peer"] = peer
		}
		components = append(components, map[string]any{
			"name":   port.Name,
			"config": map[string]any{"name": port.Name},
			"state": map[string]any{
				"name": port.Name,
				"type": "OCP_OCS_PORT",
			},
			"ocp-ocs-port": map[string]any{
				"state": map[string]any{
					"enabled":    true,
					"index":      port.Index,
					"status":     portStatus,
					"connection": connectionState,
				},
			},
		})
	}

	connections := make([]any, 0, len(snapshot.Connections))
	for _, connection := range snapshot.Connections {
		config := map[string]any{
			"connection-name": connection.Name,
			"bidirectional":   connection.Bidirectional,
			"near-port-name":  connection.NearPortName,
			"far-port-name":   connection.FarPortName,
		}
		state := map[string]any{
			"connection-name": connection.Name,
			"bidirectional":   connection.Bidirectional,
			"near-port-name":  connection.NearPortName,
			"far-port-name":   connection.FarPortName,
			"status":          connection.Status,
		}
		connections = append(connections, map[string]any{
			"connection-name": connection.Name,
			"config":          config,
			"state":           state,
		})
	}

	return map[string]any{
		"oc-optical-switch:optical-switch": map[string]any{
			"config": map[string]any{},
			"state":  map[string]any{},
			"port-connection-recovery": map[string]any{
				"state": map[string]any{
					"port-connection-recovery-capability": "NO_RECOVERY",
				},
			},
		},
		"openconfig-platform:components": map[string]any{
			"component": components,
		},
		"oc-optical-switch-connections:optical-switch-connections": map[string]any{
			"port-connection": connections,
		},
	}
}
