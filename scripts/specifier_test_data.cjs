// Test-only catalog data. Production definitions come exclusively from releases.
const fs = require('node:fs');
const path = require('node:path');
const { createHash } = require('node:crypto');
const clean = value => String(value).normalize('NFKD').replace(/\p{M}/gu,'').toLowerCase().replace(/[^\p{L}\p{N}]+/gu,' ').trim();
const part = (id,label,query,aliases=[]) => ({id,label,query,aliases:[...new Set([query,label,...aliases].map(clean))]});
const definitions = [
    {
      id: "bicycle", label: "Bicycle", svg: true,
      aliases: ["bike", "bikes", "bicycle", "bicycles", "ebike", "e-bike", "electric bike", "road bike", "mountain bike"],
      parts: [
        part("chain", "Chain", "chain", ["chains", "master link", "chain link"]),
        part("tires", "Tires & Tubes", "tire", ["tires", "tyre", "tyres", "tube", "tubes", "flat", "flat tire"]),
        part("brakes", "Brakes & Cables", "brake", ["brakes", "brake cable", "brake cables", "brake pads"]),
        part("gears", "Gears & Derailleur", "derailleur", ["gear", "gears", "shifting", "shift", "shifter"]),
        part("wheels", "Wheels & Spokes", "wheel", ["wheels", "spoke", "spokes", "rim", "rims"]),
        part("handlebars", "Handlebars & Stem", "handlebar", ["handlebars", "stem", "grips"]),
        part("seat", "Seat & Seatpost", "seat", ["saddle", "seatpost", "seat post"]),
        part("pedals", "Pedals & Cranks", "pedal", ["pedals", "crank", "cranks", "crank arm"]),
        part("bottom-bracket", "Bottom Bracket", "bottom bracket", ["bottom brackets"]),
        part("fork", "Fork & Suspension", "fork", ["forks", "suspension"]),
        part("lights", "Lights, Bell & Rust", "light", ["lights", "bell", "rust"]),
      ],
    },
    {
      id: "arm", label: "Arm", aliases: ["arm", "arms"],
      parts: [part("pain", "Pain", "pain", ["hurts", "aches"]), part("swelling", "Swelling", "swelling", ["swollen"]),
        part("injury", "Injury or fracture", "injury", ["fracture", "broken"]), part("numbness", "Numbness", "numbness", ["tingling"]),
        part("elbow", "Elbow", "elbow"), part("wrist", "Wrist", "wrist"), part("shoulder", "Shoulder", "shoulder")],
    },
    {
      id: "laptop", label: "Laptop", aliases: ["laptop", "laptops", "computer", "macbook", "notebook"],
      parts: [part("battery", "Battery & charging", "battery", ["charging", "charger"]), part("screen", "Screen", "screen", ["display"]),
        part("keyboard", "Keyboard", "keyboard", ["keys"]), part("trackpad", "Trackpad", "trackpad", ["touchpad"]),
        part("wifi", "Wi-Fi", "wifi", ["wi fi", "wireless"]), part("fan", "Fan & overheating", "fan", ["overheating", "hot"]),
        part("power", "Power & startup", "power", ["startup", "boot"]), part("audio", "Sound", "audio", ["sound", "speaker"])],
    },
    {
      id: "phone", label: "Phone", aliases: ["phone", "phones", "smartphone", "iphone", "mobile phone"],
      parts: [part("screen", "Screen", "screen", ["display"]), part("battery", "Battery", "battery"),
        part("charging", "Charging port", "charging port", ["charger", "charging"]), part("camera", "Camera", "camera"),
        part("speaker", "Speaker & microphone", "speaker", ["microphone", "audio", "sound"]),
        part("buttons", "Buttons", "button", ["buttons", "power button"]), part("water", "Water damage", "water damage", ["water", "wet"])],
    },
    {
      id: "car", label: "Car", aliases: ["car", "cars", "truck", "vehicle"],
      parts: [part("battery", "Battery", "battery"), part("tires", "Tires", "tire", ["tires", "tyre", "flat"]),
        part("brakes", "Brakes", "brake", ["brakes"]), part("engine", "Engine", "engine"), part("oil", "Oil & leaks", "oil", ["leak", "leaks"]),
        part("lights", "Lights", "light", ["lights", "headlight"]), part("starter", "Starting", "starter", ["starting", "start"]),
        part("wipers", "Windshield wipers", "wiper", ["wipers"])],
    },
    {
      id: "stove", label: "Stove", aliases: ["stove", "stoves", "oven", "range", "cooktop"],
      parts: [part("burner", "Burners", "burner", ["burners"]), part("igniter", "Igniter", "igniter", ["ignition"]),
        part("element", "Heating element", "heating element", ["element", "not heating"]), part("knob", "Knobs & controls", "knob", ["knobs", "controls"]),
        part("door", "Oven door", "door", ["hinge"]), part("thermostat", "Temperature", "thermostat", ["temperature"])],
    },
    {
      id: "toilet", label: "Toilet", aliases: ["toilet", "toilets"],
      parts: [part("clog", "Clogged drain", "clog", ["clogged", "blocked"]), part("flapper", "Flapper & running water", "flapper", ["running"]),
        part("fill-valve", "Fill valve", "fill valve"), part("leak", "Leaks", "leak", ["leaks", "leaking"]),
        part("handle", "Flush handle", "handle", ["flush", "flushing"]), part("seat", "Seat", "seat")],
    },
    {
      id: "refrigerator", label: "Refrigerator", aliases: ["refrigerator", "refrigerators", "fridge", "freezer"],
      parts: [part("cooling", "Not cooling", "not cooling", ["warm", "cooling"]), part("seal", "Door seal", "door seal", ["seal", "gasket"]),
        part("ice", "Ice maker", "ice maker", ["ice"]), part("leak", "Leaks", "leak", ["leaks", "leaking"]),
        part("fan", "Fan & noise", "fan", ["noise", "noisy"]), part("thermostat", "Thermostat", "thermostat", ["temperature"])],
    },
    {
      id: "jeans", label: "Jeans", aliases: ["jeans", "pants", "trousers", "denim"],
      parts: [part("hole", "Holes & tears", "hole", ["holes", "tear", "tears", "ripped"]), part("zipper", "Zipper", "zipper", ["zip"]),
        part("button", "Button", "button", ["buttons"]), part("seam", "Seams", "seam", ["seams"]),
        part("hem", "Hem", "hem", ["hemming"]), part("pocket", "Pockets", "pocket", ["pockets"])],
    },
  ];
const broadNouns = new Set(("arm leg hand foot feet knee shoulder elbow wrist ankle finger toe back neck head tooth teeth eye ear " +
    "car truck van motorcycle bicycle bike scooter engine tire wheel boat " +
    "laptop computer desktop macbook phone iphone smartphone tablet ipad screen keyboard mouse printer camera radio speaker headphones watch " +
    "stove oven refrigerator fridge freezer dishwasher washer dryer microwave kettle toaster blender mixer vacuum lamp light clock iron " +
    "sink faucet toilet shower door window roof wall floor chair table desk sofa couch bed furniture " +
    "jacket coat jeans pants shirt dress shoe shoes boot boots bag backpack zipper " +
    "relationship marriage friendship family resume cv code website app software").split(/\s+/));
  for (const name of ["washing machine", "air conditioner", "coffee maker", "game console", "water heater", "mobile phone", "sewing machine"]) broadNouns.add(name);
function bicycleArt() {
  const file = path.join(__dirname, 'data/approved-bicycle-art.json');
  return {version:1,id:'bicycle',...JSON.parse(fs.readFileSync(file, 'utf8'))};
}
function artURL(art) { return '/api/art/'+createHash('sha256').update(JSON.stringify(art)).digest('hex')+'.json'; }
function releaseFixture({specifics=true,art=true}={}) {
  const rows = structuredClone(definitions).map(row=>({...row,guideCount:1,parts:specifics?row.parts:[],artUrl:art&&row.id==='bicycle'?artURL(bicycleArt()):null}));
  const known = new Set(rows.flatMap(row=>row.aliases));
  for(const noun of [...broadNouns,'dick']) if(!known.has(noun)) rows.push({id:'test-'+noun.replaceAll(' ','-'),label:noun,aliases:[noun],guideCount:1,parts:[],artUrl:null});
  return {version:1,revision:'fixture',archetypes:rows,models:[{name:'MacBook A2179',nounId:'laptop'},{name:'SM-G991B',nounId:'phone'}]};
}
module.exports={releaseFixture,bicycleArt,artURL};
