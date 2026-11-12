{{- define "deploy-api.name" -}}deploy-api{{- end -}}
{{- define "deploy-api.fullname" -}}{{ .Release.Name }}-{{ include "deploy-api.name" . }}{{- end -}}
{{- define "deploy-api.labels" -}}
app.kubernetes.io/name: {{ include "deploy-api.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{- end -}}
{{- define "deploy-api.selectorLabels" -}}
app.kubernetes.io/name: {{ include "deploy-api.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}
{{- define "deploy-api.secretName" -}}
{{- if .Values.jwt.existingSecret -}}{{ .Values.jwt.existingSecret }}{{- else -}}{{ include "deploy-api.fullname" . }}-jwt{{- end -}}
{{- end -}}
