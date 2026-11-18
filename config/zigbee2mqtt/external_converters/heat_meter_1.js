const exposes = require('zigbee-herdsman-converters/lib/exposes');
const reporting = require('zigbee-herdsman-converters/lib/reporting');
const e = exposes.presets;
const ea = exposes.access;

const fzLocal = {
    esp32c6_heat_meter: {
        cluster: 'genAnalogInput',
        type: ['attributeReport', 'readResponse'],
        convert: (model, msg, publish, options, meta) => {
            if (msg.data.presentValue === undefined) return;
            const val = Number(msg.data.presentValue);
            const epId = msg.endpoint.ID;

            switch (epId) {
                case 1: return { temperature_1: Number(val.toFixed(2)) };
                case 2: return { temperature_2: Number(val.toFixed(2)) };
                case 3: return { flow: Number(val.toFixed(3)) };
                case 4: return { power: Number(val.toFixed(2)) };
                case 5: return { energy: Number(val.toFixed(5)) };
            }
        },
    },
};

const definition = {
    zigbeeModel: ['ESP32C6_HEAT_METER_V1'],
    model: 'ESP32C6_HEAT_METER_V1',
    vendor: 'HeatTech',
    description: 'ESP32-C6 Ultrasonic/Turbine Heat Meter',
    fromZigbee: [fzLocal.esp32c6_heat_meter],
    toZigbee: [],
    exposes: [
        e.numeric('temperature_1', ea.STATE).withUnit('°C').withDescription('Temperatura zasilania'),
        e.numeric('temperature_2', ea.STATE).withUnit('°C').withDescription('Temperatura powrotu'),
        e.numeric('flow', ea.STATE).withUnit('l/min').withDescription('Przepływ'),
        e.numeric('power', ea.STATE).withUnit('W').withDescription('Moc cieplna'),
        e.numeric('energy', ea.STATE).withUnit('kWh').withDescription('Energia skumulowana'),
    ],
    meta: { multiEndpoint: true },
    endpoint: (device) => ({ ep1: 1, ep2: 2, ep3: 3, ep4: 4, ep5: 5 }),
    configure: async (device, coordinatorEndpoint, logger) => {
        for (const epNum of [1, 2, 3, 4, 5]) {
            const ep = device.getEndpoint(epNum);
            if (ep) await reporting.bind(ep, coordinatorEndpoint, ['genAnalogInput']);
        }
    },
};

module.exports = definition;
