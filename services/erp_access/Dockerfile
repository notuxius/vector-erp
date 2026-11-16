FROM golang:1.26-alpine AS build
WORKDIR /src
COPY main.go login.html ./
RUN CGO_ENABLED=0 go build -trimpath -o /erp-access main.go

FROM scratch
COPY --from=build /erp-access /erp-access
USER 65532:65532
ENTRYPOINT ["/erp-access"]
