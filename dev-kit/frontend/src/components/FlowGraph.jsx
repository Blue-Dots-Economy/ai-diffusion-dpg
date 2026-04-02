import React, { useEffect } from 'react'
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  useEdgesState,
  useNodesState,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'

const NODE_COLORS = {
  start: '#16a34a',
  end: '#dc2626',
  normal: '#2563eb',
}

function toFlowNodes(nodes) {
  return nodes.map((n, i) => ({
    id: n.id,
    data: { label: n.name || n.id },
    position: { x: 200 * (i % 4), y: 150 * Math.floor(i / 4) },
    style: {
      background: NODE_COLORS[n.type] || NODE_COLORS.normal,
      color: '#fff',
      border: 'none',
      borderRadius: '10px',
      padding: '10px 16px',
      fontSize: '12px',
      fontWeight: '600',
    },
  }))
}

function toFlowEdges(edges) {
  return edges.map((e, i) => ({
    id: `e-${i}`,
    source: e.from,
    target: e.to,
    label: e.intent,
    labelStyle: { fontSize: 10, fill: '#9ca3af' },
    style: { stroke: '#4b5563' },
    markerEnd: { type: 'arrowclosed', color: '#4b5563' },
  }))
}

export default function FlowGraph({ graph }) {
  const [nodes, setNodes, onNodesChange] = useNodesState([])
  const [edges, setEdges, onEdgesChange] = useEdgesState([])

  useEffect(() => {
    if (!graph) return
    setNodes(toFlowNodes(graph.nodes || []))
    setEdges(toFlowEdges(graph.edges || []))
  }, [graph])

  if (!graph || graph.nodes?.length === 0) {
    return (
      <div className="flex items-center justify-center h-full text-gray-500 text-sm">
        No subagents yet. Start the Workflow phase to see the graph.
      </div>
    )
  }

  return (
    <div className="h-full w-full">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        fitView
        colorMode="dark"
      >
        <Background color="#374151" />
        <Controls />
        <MiniMap nodeColor={(n) => n.style?.background || '#2563eb'} />
      </ReactFlow>
    </div>
  )
}
