# ---------------------------------------------------------------------------
#  configurar_postgres.ps1
#  Prepara PostgreSQL para la aplicación de paperbet:
#    1) fija la contraseña del superusuario 'postgres'
#    2) crea la base de datos 'paperbet' si no existe
#
#  Uso:  powershell -ExecutionPolicy Bypass -File configurar_postgres.ps1
#
#  Si psql falla por autenticación, relanza esta consola como Administrador
#  (clic derecho -> "Ejecutar como administrador") y repítela.
# ---------------------------------------------------------------------------
$ErrorActionPreference = "Stop"

$raiz   = "C:\Program Files\PostgreSQL\17"
$psql   = Join-Path $raiz "bin\psql.exe"
$usuario = "postgres"
$clave   = "postgres"
$base    = "paperbet"

if (-not (Test-Path $psql)) {
    Write-Host "No se encontró psql en $psql" -ForegroundColor Red
    exit 1
}
Write-Host "psql: $psql"

function Probar-Conexion {
    param([string]$Comando)
    & $psql -U $usuario -w -h 127.0.0.1 -p 5432 -c $Comando 2>&1
    return $LASTEXITCODE -eq 0
}

Write-Host "`n[1/3] Fijando la contraseña del superusuario..." -ForegroundColor Cyan
$pgEnv = @{ PGPASSWORD = "" }
& $psql -U $usuario -w -h 127.0.0.1 -p 5432 -c "ALTER USER postgres PASSWORD '$clave';" 2>&1 | Write-Host

# reintenta con la contraseña recién puesta
$env:PGPASSWORD = $clave
Write-Host "`n[2/3] Comprobando conexión con la nueva contraseña..." -ForegroundColor Cyan
if (-not (Probar-Conexion "SELECT version();")) {
    Write-Host ""
    Write-Host "No pude autenticar como '$usuario'." -ForegroundColor Yellow
    Write-Host "Soluciones posibles:" -ForegroundColor Yellow
    Write-Host "  a) Ejecutar esta consola como Administrador y repetir el script, o"
    Write-Host "  b) En el instalador de PostgreSQL fijar la contraseña del superusuario,"
    Write-Host "     y luego poner esa misma contraseña en config.json."
    exit 2
}

Write-Host "`n[3/3] Creando la base de datos '$base'..." -ForegroundColor Cyan
$existe = & $psql -U $usuario -w -h 127.0.0.1 -p 5432 -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='$base';"
if ($existe -eq "1") {
    Write-Host "   La base '$base' ya existe."
} else {
    & $psql -U $usuario -w -h 127.0.0.1 -p 5432 -d postgres -c "CREATE DATABASE $base;"
    Write-Host "   Base '$base' creada."
}

Write-Host "`nListo. Abre la aplicación y pulsa el menú 'Base de datos -> Probar conexión'.`n" -ForegroundColor Green
