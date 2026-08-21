"""Generic (SAE J2012) diagnostic trouble code descriptions.

``DESCRIPTIONS`` holds explicit entries; ``CAUSES`` adds likely culprits for the
codes people actually see. Codes not listed here still resolve to a useful
system description via :func:`cardiag.dtc.describe`, which falls back to the
code-range tables in this module.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Explicit descriptions for common generic codes
# ---------------------------------------------------------------------------

DESCRIPTIONS: dict[str, str] = {
    # Fuel and air metering
    "P0100": "Mass or volume air flow circuit malfunction",
    "P0101": "Mass or volume air flow circuit range/performance problem",
    "P0102": "Mass or volume air flow circuit low input",
    "P0103": "Mass or volume air flow circuit high input",
    "P0106": "Manifold absolute pressure/barometric pressure circuit range/performance",
    "P0107": "Manifold absolute pressure/barometric pressure circuit low input",
    "P0108": "Manifold absolute pressure/barometric pressure circuit high input",
    "P0110": "Intake air temperature circuit malfunction",
    "P0111": "Intake air temperature circuit range/performance problem",
    "P0112": "Intake air temperature circuit low input",
    "P0113": "Intake air temperature circuit high input",
    "P0115": "Engine coolant temperature circuit malfunction",
    "P0116": "Engine coolant temperature circuit range/performance problem",
    "P0117": "Engine coolant temperature circuit low input",
    "P0118": "Engine coolant temperature circuit high input",
    "P0119": "Engine coolant temperature circuit intermittent",
    "P0120": "Throttle/pedal position sensor A circuit malfunction",
    "P0121": "Throttle/pedal position sensor A circuit range/performance problem",
    "P0122": "Throttle/pedal position sensor A circuit low input",
    "P0123": "Throttle/pedal position sensor A circuit high input",
    "P0125": "Insufficient coolant temperature for closed loop fuel control",
    "P0128": "Coolant thermostat below regulating temperature",
    "P0130": "Oxygen sensor circuit malfunction (bank 1 sensor 1)",
    "P0131": "Oxygen sensor circuit low voltage (bank 1 sensor 1)",
    "P0132": "Oxygen sensor circuit high voltage (bank 1 sensor 1)",
    "P0133": "Oxygen sensor circuit slow response (bank 1 sensor 1)",
    "P0134": "Oxygen sensor circuit no activity detected (bank 1 sensor 1)",
    "P0135": "Oxygen sensor heater circuit malfunction (bank 1 sensor 1)",
    "P0136": "Oxygen sensor circuit malfunction (bank 1 sensor 2)",
    "P0137": "Oxygen sensor circuit low voltage (bank 1 sensor 2)",
    "P0138": "Oxygen sensor circuit high voltage (bank 1 sensor 2)",
    "P0139": "Oxygen sensor circuit slow response (bank 1 sensor 2)",
    "P0140": "Oxygen sensor circuit no activity detected (bank 1 sensor 2)",
    "P0141": "Oxygen sensor heater circuit malfunction (bank 1 sensor 2)",
    "P0150": "Oxygen sensor circuit malfunction (bank 2 sensor 1)",
    "P0151": "Oxygen sensor circuit low voltage (bank 2 sensor 1)",
    "P0152": "Oxygen sensor circuit high voltage (bank 2 sensor 1)",
    "P0153": "Oxygen sensor circuit slow response (bank 2 sensor 1)",
    "P0154": "Oxygen sensor circuit no activity detected (bank 2 sensor 1)",
    "P0155": "Oxygen sensor heater circuit malfunction (bank 2 sensor 1)",
    "P0156": "Oxygen sensor circuit malfunction (bank 2 sensor 2)",
    "P0157": "Oxygen sensor circuit low voltage (bank 2 sensor 2)",
    "P0158": "Oxygen sensor circuit high voltage (bank 2 sensor 2)",
    "P0161": "Oxygen sensor heater circuit malfunction (bank 2 sensor 2)",
    "P0170": "Fuel trim malfunction (bank 1)",
    "P0171": "System too lean (bank 1)",
    "P0172": "System too rich (bank 1)",
    "P0173": "Fuel trim malfunction (bank 2)",
    "P0174": "System too lean (bank 2)",
    "P0175": "System too rich (bank 2)",
    "P0180": "Fuel temperature sensor A circuit malfunction",
    "P0190": "Fuel rail pressure sensor circuit malfunction",
    "P0191": "Fuel rail pressure sensor circuit range/performance",
    "P0193": "Fuel rail pressure sensor circuit high input",
    # Injector circuits
    "P0201": "Injector circuit malfunction - cylinder 1",
    "P0202": "Injector circuit malfunction - cylinder 2",
    "P0203": "Injector circuit malfunction - cylinder 3",
    "P0204": "Injector circuit malfunction - cylinder 4",
    "P0205": "Injector circuit malfunction - cylinder 5",
    "P0206": "Injector circuit malfunction - cylinder 6",
    "P0207": "Injector circuit malfunction - cylinder 7",
    "P0208": "Injector circuit malfunction - cylinder 8",
    "P0217": "Engine over temperature condition",
    "P0219": "Engine overspeed condition",
    "P0221": "Throttle/pedal position sensor B circuit range/performance",
    "P0223": "Throttle/pedal position sensor B circuit high input",
    "P0230": "Fuel pump primary circuit malfunction",
    "P0234": "Engine overboost condition",
    "P0243": "Turbocharger wastegate solenoid A malfunction",
    "P0245": "Turbocharger wastegate solenoid A low",
    "P0299": "Turbocharger/supercharger underboost",
    # Ignition and misfire
    "P0300": "Random or multiple cylinder misfire detected",
    "P0301": "Cylinder 1 misfire detected",
    "P0302": "Cylinder 2 misfire detected",
    "P0303": "Cylinder 3 misfire detected",
    "P0304": "Cylinder 4 misfire detected",
    "P0305": "Cylinder 5 misfire detected",
    "P0306": "Cylinder 6 misfire detected",
    "P0307": "Cylinder 7 misfire detected",
    "P0308": "Cylinder 8 misfire detected",
    "P0315": "Crankshaft position system variation not learned",
    "P0325": "Knock sensor 1 circuit malfunction (bank 1)",
    "P0327": "Knock sensor 1 circuit low input (bank 1)",
    "P0328": "Knock sensor 1 circuit high input (bank 1)",
    "P0330": "Knock sensor 2 circuit malfunction (bank 2)",
    "P0335": "Crankshaft position sensor A circuit malfunction",
    "P0336": "Crankshaft position sensor A circuit range/performance",
    "P0340": "Camshaft position sensor circuit malfunction",
    "P0341": "Camshaft position sensor circuit range/performance",
    "P0344": "Camshaft position sensor circuit intermittent",
    "P0351": "Ignition coil A primary/secondary circuit malfunction",
    "P0352": "Ignition coil B primary/secondary circuit malfunction",
    "P0353": "Ignition coil C primary/secondary circuit malfunction",
    "P0354": "Ignition coil D primary/secondary circuit malfunction",
    "P0355": "Ignition coil E primary/secondary circuit malfunction",
    "P0356": "Ignition coil F primary/secondary circuit malfunction",
    # Emissions control
    "P0400": "Exhaust gas recirculation flow malfunction",
    "P0401": "Exhaust gas recirculation flow insufficient detected",
    "P0402": "Exhaust gas recirculation flow excessive detected",
    "P0403": "Exhaust gas recirculation circuit malfunction",
    "P0404": "Exhaust gas recirculation circuit range/performance",
    "P0405": "Exhaust gas recirculation sensor A circuit low",
    "P0410": "Secondary air injection system malfunction",
    "P0411": "Secondary air injection system incorrect flow detected",
    "P0412": "Secondary air injection system switching valve A circuit malfunction",
    "P0420": "Catalyst system efficiency below threshold (bank 1)",
    "P0421": "Warm up catalyst efficiency below threshold (bank 1)",
    "P0430": "Catalyst system efficiency below threshold (bank 2)",
    "P0431": "Warm up catalyst efficiency below threshold (bank 2)",
    "P0440": "Evaporative emission control system malfunction",
    "P0441": "Evaporative emission control system incorrect purge flow",
    "P0442": "Evaporative emission control system leak detected (small leak)",
    "P0443": "Evaporative emission control system purge control valve circuit malfunction",
    "P0446": "Evaporative emission control system vent control circuit malfunction",
    "P0449": "Evaporative emission control system vent valve/solenoid circuit malfunction",
    "P0451": "Evaporative emission control system pressure sensor range/performance",
    "P0452": "Evaporative emission control system pressure sensor low input",
    "P0453": "Evaporative emission control system pressure sensor high input",
    "P0455": "Evaporative emission control system leak detected (gross leak)",
    "P0456": "Evaporative emission control system leak detected (very small leak)",
    "P0457": "Evaporative emission control system leak detected (fuel cap loose or off)",
    # Speed, idle and auxiliary inputs
    "P0500": "Vehicle speed sensor malfunction",
    "P0501": "Vehicle speed sensor range/performance",
    "P0505": "Idle control system malfunction",
    "P0506": "Idle control system RPM lower than expected",
    "P0507": "Idle control system RPM higher than expected",
    "P0521": "Engine oil pressure sensor/switch range/performance",
    "P0562": "System voltage low",
    "P0563": "System voltage high",
    "P0571": "Cruise control/brake switch A circuit malfunction",
    # Computer and output circuits
    "P0600": "Serial communication link malfunction",
    "P0601": "Internal control module memory check sum error",
    "P0602": "Control module programming error",
    "P0603": "Internal control module keep alive memory error",
    "P0604": "Internal control module random access memory error",
    "P0605": "Internal control module read only memory error",
    "P0606": "Control module processor fault",
    "P0620": "Generator control circuit malfunction",
    "P0625": "Generator field/F terminal circuit low",
    "P0630": "VIN not programmed or mismatched",
    # Transmission
    "P0700": "Transmission control system malfunction",
    "P0701": "Transmission control system range/performance",
    "P0703": "Torque converter/brake switch B circuit malfunction",
    "P0705": "Transmission range sensor circuit malfunction",
    "P0706": "Transmission range sensor circuit range/performance",
    "P0710": "Transmission fluid temperature sensor circuit malfunction",
    "P0715": "Input/turbine speed sensor circuit malfunction",
    "P0720": "Output speed sensor circuit malfunction",
    "P0725": "Engine speed input circuit malfunction",
    "P0730": "Incorrect gear ratio",
    "P0731": "Gear 1 incorrect ratio",
    "P0732": "Gear 2 incorrect ratio",
    "P0733": "Gear 3 incorrect ratio",
    "P0734": "Gear 4 incorrect ratio",
    "P0740": "Torque converter clutch circuit malfunction",
    "P0741": "Torque converter clutch circuit performance or stuck off",
    "P0743": "Torque converter clutch circuit electrical",
    "P0750": "Shift solenoid A malfunction",
    "P0755": "Shift solenoid B malfunction",
    "P0760": "Shift solenoid C malfunction",
    # Common network codes
    "U0001": "High speed CAN communication bus",
    "U0100": "Lost communication with engine control module",
    "U0101": "Lost communication with transmission control module",
    "U0121": "Lost communication with anti-lock brake system control module",
    "U0140": "Lost communication with body control module",
    "U0155": "Lost communication with instrument panel cluster control module",
    "U0401": "Invalid data received from engine control module",
    # Common chassis codes
    "C0035": "Left front wheel speed sensor circuit",
    "C0040": "Right front wheel speed sensor circuit",
    "C0045": "Left rear wheel speed sensor circuit",
    "C0050": "Right rear wheel speed sensor circuit",
}


# ---------------------------------------------------------------------------
# Likely causes, cheapest and most probable first
# ---------------------------------------------------------------------------

CAUSES: dict[str, list[str]] = {
    "P0101": [
        "Dirty or contaminated mass air flow sensor element",
        "Air leak between the MAF sensor and the throttle body",
        "Split or disconnected intake boot",
        "Clogged air filter",
    ],
    "P0128": [
        "Thermostat stuck open (by far the most common)",
        "Faulty coolant temperature sensor",
        "Low coolant level",
    ],
    "P0133": [
        "Aged upstream oxygen sensor (typical past 150,000 km)",
        "Exhaust leak upstream of the sensor",
        "Contaminated sensor from oil or coolant burning",
    ],
    "P0171": [
        "Vacuum leak: intake gasket, PCV hose, or a split intake boot",
        "Dirty mass air flow sensor",
        "Weak fuel pump or clogged fuel filter",
        "Leaking or stuck-open purge valve",
    ],
    "P0172": [
        "Leaking fuel injector",
        "Excessive fuel pressure - failed regulator",
        "Dirty air filter restricting airflow",
        "Faulty coolant temperature sensor reporting cold",
    ],
    "P0174": [
        "Vacuum leak on bank 2",
        "Dirty mass air flow sensor",
        "Weak fuel delivery",
    ],
    "P0300": [
        "Worn spark plugs or ignition leads",
        "Failing ignition coil",
        "Vacuum leak causing a lean misfire",
        "Low fuel pressure",
        "Low compression across multiple cylinders",
    ],
    "P0301": [
        "Spark plug or coil on cylinder 1",
        "Faulty injector on cylinder 1",
        "Low compression on cylinder 1",
    ],
    "P0302": [
        "Spark plug or coil on cylinder 2",
        "Faulty injector on cylinder 2",
        "Low compression on cylinder 2",
    ],
    "P0303": [
        "Spark plug or coil on cylinder 3",
        "Faulty injector on cylinder 3",
        "Low compression on cylinder 3",
    ],
    "P0304": [
        "Spark plug or coil on cylinder 4",
        "Faulty injector on cylinder 4",
        "Low compression on cylinder 4",
    ],
    "P0335": [
        "Failed crankshaft position sensor",
        "Damaged reluctor ring",
        "Wiring damage or oil contamination at the connector",
    ],
    "P0340": [
        "Failed camshaft position sensor",
        "Timing chain or belt stretched or jumped",
        "Wiring fault at the sensor connector",
    ],
    "P0401": [
        "Carbon blocking the EGR passages",
        "Stuck EGR valve",
        "Faulty differential pressure sensor",
    ],
    "P0420": [
        "Aged catalytic converter",
        "Failing downstream oxygen sensor",
        "Exhaust leak before or at the catalyst",
        "Engine running rich or burning oil, poisoning the catalyst",
    ],
    "P0430": [
        "Aged catalytic converter on bank 2",
        "Failing downstream oxygen sensor on bank 2",
        "Exhaust leak on bank 2",
    ],
    "P0442": [
        "Loose or worn fuel cap seal",
        "Cracked EVAP hose",
        "Leaking purge or vent valve",
    ],
    "P0455": [
        "Fuel cap missing, loose or failed",
        "Disconnected or split EVAP hose",
        "Vent valve stuck open",
    ],
    "P0456": [
        "Fuel cap seal weeping",
        "Pinhole leak in an EVAP hose",
        "Leaking purge valve",
    ],
    "P0457": [
        "Fuel cap left loose after refuelling - tighten it and clear the code",
        "Damaged fuel cap seal",
    ],
    "P0506": [
        "Carbon build-up in the throttle body",
        "Dirty or failing idle air control valve",
        "Vacuum leak",
    ],
    "P0507": [
        "Vacuum leak (most common)",
        "Dirty throttle body needing an idle relearn",
        "Stuck-open purge valve",
    ],
    "P0562": [
        "Failing alternator or worn drive belt",
        "Corroded battery terminals",
        "Battery at end of life",
    ],
    "P0563": [
        "Faulty voltage regulator in the alternator",
        "Poor earth strap connection",
    ],
    "P0741": [
        "Worn torque converter clutch",
        "Low or degraded transmission fluid",
        "Failed solenoid or valve body wear",
    ],
    "U0100": [
        "Poor connection or corrosion at the engine control module",
        "CAN bus wiring fault",
        "Failed engine control module",
    ],
}


# ---------------------------------------------------------------------------
# Fallback tables - keyed by code range, used when a code is not listed above
# ---------------------------------------------------------------------------

SYSTEM_BY_LETTER = {
    "P": "Powertrain (engine and transmission)",
    "C": "Chassis (brakes, steering, suspension)",
    "B": "Body (interior, comfort and safety systems)",
    "U": "Network and vehicle integration",
}

#: Second character: 0 and 2 are SAE-generic, 1 and 3 are manufacturer specific.
ORIGIN_BY_DIGIT = {
    "0": "generic (SAE standard)",
    "1": "manufacturer specific",
    "2": "manufacturer specific",
    "3": "manufacturer specific",
}

#: Third character of a generic P code -> the subsystem it belongs to.
P_SUBSYSTEM = {
    "0": "fuel and air metering, plus auxiliary emission controls",
    "1": "fuel and air metering",
    "2": "fuel and air metering (injector circuits)",
    "3": "ignition system or misfire",
    "4": "auxiliary emission controls",
    "5": "vehicle speed control, idle control and auxiliary inputs",
    "6": "computer output circuits and module communications",
    "7": "transmission",
    "8": "transmission",
    "9": "transmission control and gearbox actuators",
    "A": "hybrid propulsion",
    "B": "hybrid propulsion",
    "C": "hybrid propulsion",
}

#: Codes that mean the engine is being actively damaged or the car is unsafe.
CRITICAL_CODES = {
    "P0217",  # engine overheating
    "P0219",  # engine overspeed
    "P0234",  # overboost
    "P0300",  # misfire can destroy the catalyst
    "P0521",  # oil pressure
    "P0606",  # ECU processor fault
}
