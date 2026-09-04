import { staticHtmlAdapter } from "./staticHtml.js";
import { sbiAdapter } from "./sbi.js";
import { miraeAdapter } from "./mirae.js";
import { barodaAdapter } from "./barodaBnp.js";
import { hdfcAdapter } from "./hdfc.js";
import { utiAdapter } from "./uti.js";
import { iciciAdapter } from "./icici.js";
import { axisAdapter } from "./axis.js";
import { quantAdapter } from "./quant.js";
import { whiteoakAdapter } from "./whiteoak.js";
import { njAdapter } from "./nj.js";

/** @type {Record<string, { id: string, listFiles: Function }>} */
export const adapters = {
  static_html: staticHtmlAdapter,
  sbi_sitefinity: sbiAdapter,
  mirae_ajax: miraeAdapter,
  baroda_ajax: barodaAdapter,
  hdfc_cms: hdfcAdapter,
  uti_api: utiAdapter,
  icici_nms: iciciAdapter,
  axis_cms: axisAdapter,
  quant_aspx: quantAdapter,
  whiteoak_cms: whiteoakAdapter,
  nj_downloads: njAdapter,
};

export function getAdapter(name) {
  const a = adapters[name];
  if (!a) throw new Error(`Unknown adapter: ${name}`);
  return a;
}

export function listAdapterIds() {
  return Object.keys(adapters);
}
