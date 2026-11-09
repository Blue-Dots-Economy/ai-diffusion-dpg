{{/*
One PersistentVolumeClaim per persistence entry, named <name>-<key>:
  persistence:
    <key>: {mountPath, size, storageClass, accessMode}
Usage: templates/pvc.yaml: {{ include "dpg-common.pvcs" . }}
*/}}
{{- define "dpg-common.pvcs" -}}
{{- $name := include "dpg-common.fullname" . -}}
{{- range $key, $pv := .Values.persistence }}
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: {{ $name }}-{{ $key }}
  namespace: {{ $.Release.Namespace }}
  labels:
    app: {{ $name }}
spec:
  accessModes:
    - {{ $pv.accessMode | default "ReadWriteOnce" }}
  resources:
    requests:
      storage: {{ required (printf "persistence.%s.size is required" $key) $pv.size }}
  {{- with $pv.storageClass }}
  storageClassName: {{ . }}
  {{- end }}
{{- end }}
{{- end -}}
