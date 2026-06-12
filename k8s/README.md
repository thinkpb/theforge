# Forge on Kubernetes — first manifests

The gateway only; Postgres, Redis, and (optionally) Ollama are expected to
exist in-cluster or as managed services — point the ConfigMap/Secret URLs at
them. The full self-contained deployment (operators, tenant isolation, Helm
chart) is the Phase 5 milestone; these manifests are the stepping stone.

```bash
# build the image where your cluster can see it (kind/minikube: load it in)
docker build -t forge-gateway:0.1.0 .
minikube image load forge-gateway:0.1.0   # if using minikube

# secrets first (copy, edit, apply)
cp secret.example.yaml secret.yaml && $EDITOR secret.yaml
kubectl apply -f secret.yaml

# everything else
kubectl apply -k .

# run migrations against the database (one-off)
kubectl run -n forge forge-migrate --rm -it --restart=Never \
  --image=forge-gateway:0.1.0 \
  --overrides='{"spec":{"containers":[{"name":"forge-migrate","image":"forge-gateway:0.1.0","command":["/app/.venv/bin/alembic","upgrade","head"],"envFrom":[{"secretRef":{"name":"forge-gateway-secrets"}}]}]}}'

# smoke test
kubectl -n forge port-forward svc/forge-gateway 8000:80 &
curl http://localhost:8000/health
```
