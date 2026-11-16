// Password or email entry for the public tunnel. ERP authenticates the user separately.
package main

import (
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
	_ "embed"
	"encoding/hex"
	"html/template"
	"log"
	"net/http"
	"net/http/httputil"
	"net/url"
	"os"
	"strconv"
	"strings"
	"time"
)

//go:embed login.html
var loginHTML string
var form = template.Must(template.New("login").Parse(loginHTML))

const cookieName = "__Host-vector_erp_access"

func signature(key []byte, expiry string) string {
	mac := hmac.New(sha256.New, key)
	mac.Write([]byte(expiry))
	return hex.EncodeToString(mac.Sum(nil))
}

func authorized(r *http.Request, key []byte) bool {
	cookie, err := r.Cookie(cookieName)
	if err != nil {
		return false
	}
	expiry, sig, ok := strings.Cut(cookie.Value, ".")
	t, err := strconv.ParseInt(expiry, 10, 64)
	return ok && err == nil && t > time.Now().Unix() && hmac.Equal([]byte(signature(key, expiry)), []byte(sig))
}

func main() {
	// The verifier is SHA-256 of a randomly generated 256-bit secret.
	raw, err := os.ReadFile("/run/secrets/password.sha256")
	if err != nil {
		log.Fatal("Missing access password verifier")
	}
	hash, err := hex.DecodeString(strings.TrimSpace(string(raw)))
	if err != nil || len(hash) != sha256.Size {
		log.Fatal("Invalid access password verifier")
	}
	email, err := url.Parse(os.Getenv("ERP_EMAIL_URL"))
	if err != nil || email.Scheme != "https" || !strings.HasSuffix(email.Host, ".trycloudflare.com") || email.User != nil {
		log.Fatal("Missing protected email tunnel")
	}
	key := make([]byte, 32)
	if _, err := rand.Read(key); err != nil {
		log.Fatal(err)
	}
	origin, _ := url.Parse("http://frontend:8080")
	proxy := &httputil.ReverseProxy{
		Rewrite: func(p *httputil.ProxyRequest) {
			p.SetURL(origin)
			p.Out.Host = p.In.Host
			p.SetXForwarded()
			p.Out.Header.Set("X-Forwarded-Proto", "https")
			p.Out.Header.Del("Cookie")
			for _, c := range p.In.Cookies() {
				if c.Name != cookieName {
					p.Out.AddCookie(c)
				}
			}
		},
		ModifyResponse: func(resp *http.Response) error {
			resp.Header.Set("Cache-Control", "private, no-store")
			return nil
		},
	}
	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Cache-Control", "private, no-store")
		if r.URL.Path != "/_vector_access/login" && authorized(r, key) {
			proxy.ServeHTTP(w, r)
			return
		}
		w.Header().Set("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
		w.Header().Set("Referrer-Policy", "same-origin")
		w.Header().Set("X-Robots-Tag", "noindex, nofollow")
		status, message := http.StatusOK, ""
		switch {
		case r.Method == http.MethodPost && r.URL.Path == "/_vector_access/login":
			if r.Header.Get("Origin") != "https://"+r.Host {
				http.Error(w, "Forbidden", http.StatusForbidden)
				return
			}
			r.Body = http.MaxBytesReader(w, r.Body, 1024)
			if err := r.ParseForm(); err != nil {
				http.Error(w, "Invalid form", http.StatusBadRequest)
				return
			}
			guess := sha256.Sum256([]byte(r.PostForm.Get("password")))
			if subtle.ConstantTimeCompare(guess[:], hash) == 1 {
				until := time.Now().Add(24 * time.Hour)
				expiry := strconv.FormatInt(until.Unix(), 10)
				http.SetCookie(w, &http.Cookie{Name: cookieName, Value: expiry + "." + signature(key, expiry),
					Path: "/", Expires: until, Secure: true, HttpOnly: true, SameSite: http.SameSiteLaxMode})
				http.Redirect(w, r, "/login", http.StatusSeeOther)
				return
			}
			status, message = http.StatusUnauthorized, "Невірний пароль доступу."
		case r.Method != http.MethodGet && r.Method != http.MethodHead:
			http.Error(w, "Authentication required", http.StatusUnauthorized)
			return
		}
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		w.WriteHeader(status)
		form.Execute(w, struct{ EmailURL, Error string }{email.String(), message})
	})
	server := &http.Server{Addr: ":8080", Handler: handler, ReadHeaderTimeout: 10 * time.Second, IdleTimeout: 60 * time.Second}
	log.Fatal(server.ListenAndServe())
}
