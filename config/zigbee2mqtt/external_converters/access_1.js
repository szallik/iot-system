const fz = require('zigbee-herdsman-converters/converters/fromZigbee');
const tz = require('zigbee-herdsman-converters/converters/toZigbee');
const exposes = require('zigbee-herdsman-converters/lib/exposes');
const reporting = require('zigbee-herdsman-converters/lib/reporting');
const e = exposes.presets;
const ea = exposes.access;

const fzLocal = {
    esp32c6_pin_auth_request: {
        cluster: 'genAnalogInput',
        type: ['attributeReport', 'readResponse'],
        convert: (model, msg, publish, options, meta) => {
            if (msg.data.presentValue === undefined) return;
            const epId = msg.endpoint.ID;
            const rawVal = Math.round(Number(msg.data.presentValue));

            if (!meta.device.meta) meta.device.meta = {};

            if (epId === 1) {
                meta.device.meta.pinHighEncoded = rawVal;
                return;
            }
            if (epId === 2) {
                meta.device.meta.pinLowEncoded = rawVal;
                return;
            }
            if (epId === 3) {
                const requestId = rawVal;
                if (requestId <= 0) return;

                const pinHighEncoded = meta.device.meta.pinHighEncoded || 0;
                const pinLowEncoded = meta.device.meta.pinLowEncoded || 0;

                const totalLen = Math.floor(pinHighEncoded / 10000);
                const first4Num = pinHighEncoded % 10000;

                let credential = String(first4Num).padStart(4, '0');
                if (totalLen > 4) {
                    const lowDigitsCount = totalLen - 4;
                    credential += String(pinLowEncoded).padStart(lowDigitsCount, '0');
                }

                const friendlyName = (options && options.friendly_name) ? options.friendly_name : meta.device.ieeeAddr;

                return {
                    device_id: friendlyName,
                    request_id: requestId,
                    credential: credential,
                };
            }
        },
    },
};

const tzLocal = {
    esp32c6_pin_auth_response: {
        key: ['granted', 'auth_response'],
        convertSet: async (entity, key, value, meta) => {
            const payload = (key === 'auth_response' && typeof value === 'object') ? value : meta.message;
            const requestId = Number(payload.request_id);
            const granted = Boolean(payload.granted);

            const encodedDecision = granted ? Math.abs(requestId) : -Math.abs(requestId);
            const ep1 = meta.device.getEndpoint(1);

            await ep1.write('genAnalogOutput', { presentValue: encodedDecision });

            return {
                state: {
                    last_response_request_id: requestId,
                    granted: granted,
                },
            };
        },
    },
};

const definition = {
    zigbeeModel: ['ESP32C6_PIN_PAD_V1'],
    model: 'ESP32C6_PIN_PAD_V1',
    vendor: 'AccessTech',
    description: 'ESP32-C6 PIN Access Control Module',
    fromZigbee: [fzLocal.esp32c6_pin_auth_request],
    toZigbee: [tzLocal.esp32c6_pin_auth_response],
    exposes: [
        e.text('device_id', ea.STATE),
        e.numeric('request_id', ea.STATE_SET),
        e.text('credential', ea.STATE),
        e.binary('granted', ea.STATE_SET, true, false),
    ],
    meta: { multiEndpoint: true },
    endpoint: (device) => ({ ep1: 1, ep2: 2, ep3: 3 }),
    configure: async (device, coordinatorEndpoint, logger) => {
        for (const epNum of [1, 2, 3]) {
            const ep = device.getEndpoint(epNum);
            if (ep) await reporting.bind(ep, coordinatorEndpoint, ['genAnalogInput']);
        }
        const ep1 = device.getEndpoint(1);
        if (ep1) await reporting.bind(ep1, coordinatorEndpoint, ['genAnalogOutput']);
    },
};

module.exports = definition;
