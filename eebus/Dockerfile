# ems-eebus – EEBUS-Adapter (Go, enbility/eebus-go fest gepinnt über go.mod/go.sum)
FROM golang:1.26.10 AS build
WORKDIR /src
COPY eebus/go.mod eebus/go.sum ./
RUN go mod download
COPY eebus/*.go ./
RUN go vet ./... && CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /ems-eebus .

FROM alpine:3.23
RUN apk add --no-cache ca-certificates tzdata
COPY --from=build /ems-eebus /usr/local/bin/ems-eebus
USER 1000:1000
ENTRYPOINT ["/usr/local/bin/ems-eebus"]
