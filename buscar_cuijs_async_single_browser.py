import json
import sys
import time
from datetime import datetime
import asyncio

import toxlsx, fromxlsx

from playwright.async_api import async_playwright

loginUrl = "https://eje.juscaba.gob.ar/auth/realms/IOL-CABA/protocol/openid-connect/auth?client_id=iol-ui&redirect_uri=https%3A%2F%2Feje.juscaba.gob.ar%2Fiol-ui%2Fu%2Finicio&state=a7b90261-d521-4ab2-8d7f-a636175f5018&nonce=5d2fd63a-0f29-4009-8d23-7da7988d6bbd&response_mode=fragment&response_type=code&scope=openid"
username = "27226535530"
password = "Papafrita#01"
state_file_path = "storage_state.json"
errores = 0
oks = 0
desdeXlsx = False

camposBuscados = ['ubicacion_organismo', 'fuero', 'cuij', 'numero']
outputBuffer = ""

urlPatternAdjudicacion = "https://eje.juscaba.gob.ar/iol-ui/u/expedientes?identificador=&numeroAdjudicacion=MiNumero&tipoBusqueda=CAU"


async def login_and_save_state(url, username, password, state_file_path):
    try:
        async with async_playwright() as p:
            browser = await p.firefox.launch()
            context = await browser.new_context()
            page = await context.new_page()

            await page.goto(url)
            # Perform login actions (e.g., fill username/password, click login button)
            await page.fill('input[name="username"]', username)
            await page.fill('input[name="password"]', password)
            await page.click('button[type="submit"]')
            
            # Wait for navigation or a specific element to confirm login success
            await page.wait_for_url("https://eje.juscaba.gob.ar/iol-ui/u/inicio")

            # Save the storage state (cookies and local storage) to a file
            await context.storage_state(path=state_file_path)
            await browser.close()
            return True
    except Exception as e:
        print(f"Error: {str(e)}")
        return False


async def use_saved_state(url, state_file_path):
    async with async_playwright() as p:
        browser = await p.firefox.launch()
        # Create a new context with the saved storage state
        context = await browser.new_context(storage_state=state_file_path)
        page = await context.new_page()

        await page.goto(url)
        page.on("response", lambda response: print(f"Response: {response.status} {response.headers}"))
        # You are now logged in and can perform actions requiring authentication
        print(f"Current page title: {await page.title()}")
        
        await browser.close()


async def capture_ajax_chain(url, context, max_retries=3, retry_delay=2):
    """Capture AJAX chain using an existing browser context with retry logic."""
    expId = None
    for attempt in range(max_retries):
        page = await context.new_page()
        
        requests = {}  # To store request details
        responses = {}  # To store response details
        ajax_calls = []  # To store the chain of AJAX calls
        has_404 = False

        def handle_request(request):
            if request.resource_type == "xhr" or request.resource_type == "fetch":
                requests[request.url] = {
                    "method": request.method,
                    "headers": request.headers,
                    "post_data": request.post_data,
                }

        async def handle_response(response):
            nonlocal has_404
            
            # Check for 404 errors
            if response.status == 404:
                has_404 = True
                #print(f"  ⚠️  404 error on attempt {attempt + 1}: {response.url}")
            
            if 'encabezado?expId=' in response.request.url:
                if response.request.resource_type == "xhr" or response.request.resource_type == "fetch":
                    try:
                        response_body = await response.json()
                    except Exception:
                        response_body = await response.text()

                    responses[response.request.url] = {
                        "status": response.status,
                        "headers": response.headers,
                        "body": response_body,
                    }
                    
                    # Combine request and response to form an AJAX call entry
                    request_details = requests.get(response.request.url)
                    if request_details:
                        ajax_calls.append({
                            #"response": responses[response.request.url],
                            "expId": response.request.url.split("expId=")[1]
                        })
            elif  'ficha?expId=' in response.request.url:
                #print(f"Response: {response.request.url}")
                #print (f"Response: response.request.resource_type {response.request.resource_type} ")                
                if response.request.resource_type == "xhr" or response.request.resource_type == "fetch" or  response.request.resource_type == "document":
                    try:
                        response_body = await response.json()
                        responses[response.request.url] = {
                            "status": response.status,
                            "headers": response.headers,
                            "body": response_body,
                        }
                        #print(f"Body:   {response_body}")
                        # Combine request and response to form an AJAX call entry                       
                        ajax_calls.append({
                            "response": responses[response.request.url]
                        })
                    except Exception as e:
                        response_body = await response.text()
                        print(f"Error: {str(e)}")

                        responses[response.request.url] = {
                            "status": response.status,
                            "headers": response.headers,
                            "body": response_body,
                        }
                        request_details = requests.get(response.request.url)
                        if request_details:
                            ajax_calls.append({
                                "response": responses[response.request.url]
                            })


        page.on("request", handle_request)
        page.on("response", handle_response)

        try:
            # Add longer timeout and wait for network idle
            await page.goto(url, timeout=30000, wait_until='networkidle')
            
            # Add extra wait to ensure AJAX calls complete
            await asyncio.sleep(1)
            await page.wait_for_load_state('networkidle', timeout=10000)
            
            # If we got results and no 404s, return successfully
            if ajax_calls and not has_404:
                return ajax_calls
            
            # If we got a 404 or no results, retry
            if attempt < max_retries - 1:
                print(f"  🔄 Retrying in {retry_delay} seconds... (attempt {attempt + 2}/{max_retries})")
                await asyncio.sleep(retry_delay)
            
        except Exception as e:
            print(f"  ❌ Error on attempt {attempt + 1}: {str(e)}")
            if attempt < max_retries - 1:
                print(f"  🔄 Retrying in {retry_delay} seconds...")
                await asyncio.sleep(retry_delay)
        finally:
            await page.close()
    
    # All retries exhausted
    return ajax_calls

def extraerDatos(respuesta ):
    global oks
    ret = {}    
    cuerpo = respuesta[0]["response"]["body"]
    #print (cuerpo)

    camposSimples = ['cuij','sufijo','tipoExpediente', 'caratula', 'monto','fechaInicio']
    for campo in camposSimples:
        ret[campo]= cuerpo.get(campo)
    fyh = datetime.fromtimestamp(ret['fechaInicio']/1000 )
    ret['fechaInicio'] = fyh.date()
    ret['expediente'] = str(cuerpo.get('numero'))+'/'+str(cuerpo.get('anio'))
    radicacion =  cuerpo.get('radicaciones')
    #ret['secretaria'] =  radicacion.get('secretariaPrimeraInstancia')
    rlista = radicacion.get('organismoPrimeraInstancia').split(' ')
    ret['fuero'] = ' '.join(rlista[:-2] )
    ret['juzgado numero']=rlista[-1]
    ret['secretaria'] =  radicacion.get('secretariaPrimeraInstancia').split('N°')[-1]
    objetosJuicio = cuerpo.get('objetosJuicio')
    ret['objeto'] = objetosJuicio[0].get('objetoJuicio')
    ret['materia'] = objetosJuicio[0].get('materia')
    oks += 1
    return ret


async def process_single_adjudicacion(indice, linea, context, camposBuscados):
    global errores
    """Process a single adjudication number and return the result line."""
    print(linea)
    urlConDatos = f"https://eje.juscaba.gob.ar/iol-ui/u/expedientes?identificador=&numeroAdjudicacion={linea}&tipoBusqueda=CAU"
    
    
    datosExpId = await capture_ajax_chain(urlConDatos, context)
    if len(datosExpId) > 0:
        expId = datosExpId[0]['expId']
    else:
        errores += 1
        return f"{indice}|{linea}|error\n"
    
    
    datosDeLaLinea = await capture_ajax_chain(f"https://eje.juscaba.gob.ar/iol-api/api/public/expedientes/ficha?expId={expId}", context)
 
    try:
        datos = extraerDatos(datosDeLaLinea )
        return f"{indice}|{linea}|{expId}|" + "|".join(str(v) for v in datos.values()) + "\n"
    except Exception as e:
        errores += 1
        print(f"Error: {str(e)}")
        return f"{indice}|{linea}|error2\n" 
 
##    if len(datosDeLaLinea) > 0:
#        try:
#            datos = extraerDatos(camposBuscados, datosDeLaLinea)
#            return linea + "|" + "|".join(str(v) for v in datos.values()) + "\n"
#        except Exception as e:
#            print(f"Error: {str(e)}")
#            return linea + "|error\n"
#    else:
#        return linea + "|error\n"


async def process_all_adjudicaciones(numerosDeAdjudicacion, context, camposBuscados, max_concurrent=5):
    """Process all adjudication numbers with controlled concurrency."""
    semaphore = asyncio.Semaphore(max_concurrent)
    
    async def bounded_process(indice, valor):
        async with semaphore:
            return await process_single_adjudicacion(indice, valor, context, camposBuscados)
    
    # Process all numbers concurrently with bounded parallelism
    tasks = [bounded_process(indice, valor) for indice, valor in numerosDeAdjudicacion.items()]
    results = await asyncio.gather(*tasks)
    return results

def leerAdjudicacionDesdeTxt(nombreArchivo:str):                                     
    numerosDeAdjudicacion = {}
    i=2
    try:
        with open(nombreArchivo, encoding='windows-1252') as f:
            crudos = f.readlines()

            for s in crudos:
                t = s.strip()
                if t.isdigit():
                    numerosDeAdjudicacion[i] = t
    except Exception as e:
        print("No se pudo abrir el archivo de numeros de adjudicaciones")
        print("Error: " + str(e))
        return []    
    return numerosDeAdjudicacion
    

def leerArchivoInput(nombreArchivo:str ):
    global desdeXlsx
    if nombreArchivo.split(".")[-1].lower() == 'xlsx':
        desdeXlsx = True
        return fromxlsx.leerAdjudicacionDesdeXlsx(nombreArchivo) 
    else:
        desdeXlsx = False
        return leerAdjudicacionDesdeTxt(nombreArchivo)

def leerInputs():
    if len(sys.argv) < 4:
        print("Forma de uso: python buscar_cuijs.py CUIT PASSWORD ARCHIVO_DE_ADJUDICACIONES")
        return (None, None, [])
    username = sys.argv[1]
    password = sys.argv[2]
    numeros = leerArchivoInput(sys.argv[3])
    return (username, password, numeros)
    
    
async def main():
    oks = 0
    errores = 0
    (username, password, numerosDeAdjudicacion) = leerInputs()
    if len(numerosDeAdjudicacion) == 0:
        return
      
    if not await login_and_save_state(loginUrl, username, password, state_file_path):
        return

    # Create a single browser instance and context for all operations
    async with async_playwright() as p:
        browser = await p.firefox.launch()
        context = await browser.new_context(storage_state=state_file_path)
        
        try:
            # Process all adjudication numbers concurrently using the shared context
            # Adjust max_concurrent based on your needs (default is 5 concurrent requests)
            outputBuffer = await process_all_adjudicaciones(
                numerosDeAdjudicacion, 
                context, 
                camposBuscados,
                max_concurrent=5
            )
            if (desdeXlsx):
               nroUltimaColumna, nombreUltimaColumna  =  toxlsx.encontrarUltimaColumna(None, 250, sys.argv[3])
               archivoSalida = sys.argv[3]
            else:
                primeraColumna = 0
                archivoSalida = baseNombreArchivoSalida()+"_procesado.xlsx"
                
            toxlsx.pipeCSVtoXlsx(outputBuffer,   ['asignacion','expid','cuij','sufijo','tipoExpediente', 'caratula', 'monto','fechaInicio','expediente','fuero','juzgado','secretaria','objeto','materia'] ,archivoSalida, nroUltimaColumna, desdeXlsx)
             
            with open(baseNombreArchivoSalida()+"_procesado.txt", "w") as f:
                f.write("".join(outputBuffer))
        finally:
            await browser.close()
def baseNombreArchivoSalida():
    return '.'.join(sys.argv[3].split('.')[:-1])

if __name__ == "__main__":
    comienzo = time.perf_counter()
   # muestra = ["250036046|3347831|J-01-00262489-9/2025-0|262489|2025|0|EXP|GCBA CONTRA MORENO XIOMARA JOSEFINA SOBRE EJECUCION  FISCAL - INGRESOS BRUTOS|748340.0|2025-11-25|SECRETARÍA N°15|JUZGADO DE 1RA INSTANCIA EN LO CONTENCIOSO ADMINISTRATIVO Y TRIBUTARIO Nº 8|INGRESOS BRUTOS|EJECUCION  FISCAL",
#"250036418|3347833|J-01-00262501-2/2025-0|262501|2025|0|EXP|GCBA CONTRA ZAPATAEHIJOSSRL SOBRE EJECUCION  FISCAL - INGRESOS BRUTOS|1230562.08|2025-11-25|SECRETARÍA N°8|JUZGADO DE 1RA INSTANCIA EN LO CONTENCIOSO ADMINISTRATIVO Y TRIBUTARIO Nº 4|INGRESOS BRUTOS|EJECUCION  FISCAL"]

    asyncio.run(main())
    fin = time.perf_counter()
    print(f"OKS {oks}")
    print(f"Errores: {errores}")
    print(f"Tiempo de ejecución: {fin - comienzo} segundos")