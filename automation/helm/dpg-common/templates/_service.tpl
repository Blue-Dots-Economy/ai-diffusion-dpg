{{/*
ClusterIP (or NodePort) Service in front of the deployment.
Usage: templates/service.yaml: {{ include "dpg-common.service" . }}
*/}}
{{- define "dpg-common.service" -}}
{{- $name := include "dpg-common.fullname" . -}}
apiVersion: v1
kind: Service
metadata:
  name: {{ $name }}
  namespace: {{ .Release.Namespace }}
  labels:
    app: {{ $name }}
spec:
  type: {{ .Values.service.type | default "ClusterIP" }}
  selector:
    app: {{ $name }}
  ports:
    - name: http
      port: {{ .Values.service.port }}
      targetPort: {{ .Values.service.port }}
      protocol: TCP
      {{- if and (eq (.Values.service.type | default "ClusterIP") "NodePort") .Values.service.nodePort }}
      nodePort: {{ .Values.service.nodePort }}
      {{- end }}
{{- end -}}
